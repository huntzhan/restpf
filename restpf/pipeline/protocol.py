import asyncio
from functools import wraps
from collections import (
    deque,
    namedtuple,
)

from restpf.utils.helper_functions import (
    method_named_args,
)
from restpf.utils.helper_classes import (
    TreeState,
)
from restpf.utils.helper_functions import async_call
from restpf.resource.attributes import (
    AttributeContextOperator,
)


# `state`, `input_state`, `output_state` should be instance of ResourceState.
_COLLECTION_NAMES = ['attributes', 'relationships', 'resource_id']

ResourceState = namedtuple(
    'ResourceState',
    _COLLECTION_NAMES,
)

RawOutputStateContainer = namedtuple(
    'RawOutputStateContainer',
    _COLLECTION_NAMES,
)


def _meta_build(method):
    METHOD_PREFIX = 'build_'

    attr_name = method.__name__[len(METHOD_PREFIX):]
    cls_name = attr_name.upper() + '_CLS'

    @wraps(method)
    def _wrapper(self, *args, **kwargs):
        setattr(
            self, attr_name,
            getattr(self, cls_name)(*args, **kwargs),
        )

    return _wrapper


class PipelineRunner:

    CONTEXT_RULE_CLS = None
    STATE_TREE_BUILDER_CLS = None
    REPRESENTATION_GENERATOR_CLS = None
    PIPELINE_CLS = None

    @_meta_build
    def build_context_rule(self):
        pass

    @_meta_build
    def build_state_tree_builder(self):
        pass

    @_meta_build
    def build_representation_generator(self):
        pass

    def set_resource(self, resource):
        self.resource = resource

    async def run_pipeline(self):
        pipeline = self.PIPELINE_CLS(
            resource=self.resource,
            context_rule=self.context_rule,
            state_builder=self.state_tree_builder,
            rep_generator=self.representation_generator,
        )
        await pipeline.run()
        return pipeline


class ContextRule:

    HTTPMethod = None

    def _default_validator(self, state):
        context = AttributeContextOperator(self.HTTPMethod)
        return all(map(
            lambda x: x.validate(context),
            filter(
                bool,
                [
                    state.resource_id,
                    state.attributes,
                    state.relationships,
                ],
            ),
        ))

    async def validate_input_state(self, state):
        return self._default_validator(state)

    async def validate_output_state(self, state):
        return self._default_validator(state)

    def _select_callbacks(self, query, root_attr, root_state):
        queue = deque()
        queue.append(
            (root_attr, root_state),
        )

        ret = []

        while queue:
            attr, state = queue.popleft()
            callback, options = query(attr.bh_path, self.HTTPMethod)

            if callback and (state or root_state is None):
                ret.append(
                    (callback, attr, state),
                )

            for name, child_attr in attr.bh_named_children.items():
                child_state = getattr(state, name) if state else None
                queue.append(
                    (child_attr, child_state),
                )

        return ret

    # TODO: design bugfix. add mapping from attr collection to state.
    async def select_callbacks(self, resource, state):
        '''
        return ordered collection_name -> [(callback, attr, state), ...].
        '''

        ret = {}
        for key in ['attributes', 'relationships']:
            if getattr(resource, key) is None:
                continue

            attr_collection = getattr(resource, f'{key}_obj')

            ret[key] = self._select_callbacks(
                getattr(
                    attr_collection,
                    'get_registered_callback_and_options',
                ),
                getattr(
                    attr_collection,
                    'attr_obj',
                ),
                getattr(
                    state,
                    key,
                ),
            )

        for name in _COLLECTION_NAMES:
            if name not in ret:
                ret[name] = []
        return ret

    async def callback_kwargs(self, attr, state):
        '''
        TODO
        Available keys:

        - state
        - attr
        - resource_id
        '''
        return {
            'attr': attr,
            'state': state,
        }


class ContextRuleWithResourceID(ContextRule):

    @method_named_args('raw_resource_id')
    def __init__(self):
        pass

    async def callback_kwargs(self, attr, state):
        ret = await async_call(
            super().callback_kwargs,
            attr, state,
        )
        ret.update({
            'resource_id': self.raw_resource_id,
        })
        return ret


class _ContextRuleBinder:

    @method_named_args('context_rule')
    def bind_context_rule(self):
        pass


class StateTreeBuilder(_ContextRuleBinder):

    async def build_input_state(self, resource):
        raise NotImplemented

    async def build_output_state(self, resource, raw_obj):
        raise NotImplemented


class RepresentationGenerator(_ContextRuleBinder):

    async def generate_representation(self, resource, output_state):
        return {}


def _merge_output_of_callbacks(output_of_callbacks):

    def helper(ret, child_value, tree_state):
        if not tree_state:
            # leaf node.
            return child_value if child_value else ret

        ret = ret if ret else child_value
        if not isinstance(ret, dict):
            # none leaf node with wrong ret type.
            ret = {}

        for name, child in tree_state.children:
            ret[name] = helper(
                ret.get(name), child.value, child.next,
            )
        return ret

    # deal with root.
    root_gap = output_of_callbacks.root_gap
    ret = root_gap.value if root_gap else {}

    return helper(ret, None, output_of_callbacks)


class PipelineBase:

    '''
    Pipeline based on following instances:

    - `resource`
    - `context_rule`
    - `state_builder`
    - `output_state_creator`

    Steps of pipeline:

    1. create `input_state`.
    2. validate `input_state`.
    3. generate callback list.
    4. call callbacks and collect strucutred return values.
    5. create `output_state`.
    6. validate `output_state`.
    '''

    @method_named_args(
        'resource', 'context_rule',
        'state_builder', 'rep_generator',
    )
    def __init__(self):
        '''
        1. `context_rule` holds all the states of input.
        2. other entities all reference `context_rule`.
        '''

        self.state_builder.bind_context_rule(
            context_rule=self.context_rule,
        )
        self.rep_generator.bind_context_rule(
            context_rule=self.context_rule,
        )

    async def _build_input_state(self):
        self.input_state = await async_call(
            self.state_builder.build_input_state,
            self.resource,
        )

        input_state_is_valid = await async_call(
            self.context_rule.validate_input_state,
            self.input_state,
        )
        if not input_state_is_valid:
            raise RuntimeError('TODO: input state not valid')

    async def _invoke_callbacks(self):
        name2selected = await async_call(
            self.context_rule.select_callbacks,
            self.resource, self.input_state,
        )

        async_collection_names = []
        async_nested_gathers = []
        async_nested_paths = []

        for collection_name, callback_and_options in name2selected.items():
            async_collection_names.append(collection_name)

            async_gathers = []
            async_paths = []

            for callback, attr, state in callback_and_options:
                kwargs = await async_call(
                    self.context_rule.callback_kwargs,
                    attr, state,
                )
                async_gathers.append(
                    async_call(callback, **kwargs),
                )
                async_paths.append(
                    attr.bh_path,
                )

            async_nested_gathers.append(async_gathers)
            async_nested_paths.append(async_paths)

        joined_callbacks = asyncio.gather(
            *[
                asyncio.gather(*callbacks)
                for callbacks in async_nested_gathers
            ],
        )

        name2raw_obj = {}

        for name, paths, rets in zip(
            async_collection_names,
            async_nested_paths,
            await joined_callbacks,
        ):
            tree_state = TreeState()
            for path, ret in zip(paths, rets):
                tree_state.touch(path).value = ret

            name2raw_obj[name] = _merge_output_of_callbacks(tree_state)

        self.merged_output_of_callbacks = \
            RawOutputStateContainer(**name2raw_obj)

    async def _build_output_state(self):
        self.output_state = await async_call(
            self.state_builder.build_output_state,
            self.resource, self.merged_output_of_callbacks,
        )
        output_state_is_valid = await async_call(
            self.context_rule.validate_output_state,
            self.output_state,
        )
        if not output_state_is_valid:
            raise RuntimeError('TODO: output state not valid')

    async def _generate_representation(self):
        self.representation = None
        if self.rep_generator:
            self.representation = await async_call(
                self.rep_generator.generate_representation,
                self.resource, self.output_state,
            )

    async def run(self):
        await self._build_input_state()
        await self._invoke_callbacks()
        await self._build_output_state()
        await self._generate_representation()


class SingleResourcePipeline(PipelineBase):
    pass


class MultipleResourcePipeline(PipelineBase):
    pass


# TODO: relative resource pipeline.
