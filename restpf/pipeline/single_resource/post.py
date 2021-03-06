from restpf.utils.helper_classes import (
    StateCreator,
)
from restpf.resource.attributes import (
    HTTPMethodConfig,
)
from restpf.resource.attribute_states import (
    create_attribute_state_tree_for_input,
)
from restpf.pipeline.protocol import (
    ContextRule,
    CallbackKwargsStateVariableMapper,
    CallbackKwargsVariableCollector,
    StateTreeBuilder,
    RepresentationGenerator,
    ResourceState,
    PipelineRunner,
    SingleResourcePipeline,
)


class PostSingleResourcePipelineState(metaclass=StateCreator):

    ATTRS = [
        'raw_resource_id',
        'raw_attributes',
        'raw_relationships',
    ]


class PostSingleResourceCallbackKwargsStateVariableMapper(
    CallbackKwargsStateVariableMapper,
):
    ATTR2KWARG = {
        'raw_resource_id': 'submitted_resource_id',
        'raw_attributes': 'raw_attributes',
        'raw_relationships': 'raw_relationships',
    }


class PostSingleResourceCallbackKwargsVariableCollector(
    CallbackKwargsVariableCollector,
):
    VARIABLES = [
        'generated_resource_id',
    ]


class PostSingleResourceContextRule(ContextRule):

    HTTPMethod = HTTPMethodConfig.POST


class PostSingleResourceStateTreeBuilder(StateTreeBuilder):

    PROXY_ATTRS = [
        'raw_attributes',
        'raw_relationships',
    ]

    def build_input_state(self, resource):
        return ResourceState(
            attributes=create_attribute_state_tree_for_input(
                resource.attributes_obj.attr_obj,
                self.raw_attributes,
            ),
            relationships=create_attribute_state_tree_for_input(
                resource.relationships_obj.attr_obj,
                self.raw_relationships,
            ),
            resource_id=self._get_id_state_for_input(resource),
        )

    def build_output_state(self, resource, raw_obj):
        return ResourceState(
            attributes=None,
            relationships=None,
            resource_id=None,
        )


class PostSingleResourceRepresentationGenerator(RepresentationGenerator):

    def generate_representation(self, resource, output_state):
        # no return.
        return None


class PostSingleResourcePipelineRunner(PipelineRunner):

    CALLBACK_KWARGS_CONTROLLER_CLSES = [
        PostSingleResourceCallbackKwargsStateVariableMapper,
        PostSingleResourceCallbackKwargsVariableCollector,
    ]
    CONTEXT_RULE_CLS = PostSingleResourceContextRule

    STATE_TREE_BUILDER_CLS = PostSingleResourceStateTreeBuilder
    REPRESENTATION_GENERATOR_CLS = PostSingleResourceRepresentationGenerator

    PIPELINE_CLS = SingleResourcePipeline
    PIPELINE_STATE_CLS = PostSingleResourcePipelineState
