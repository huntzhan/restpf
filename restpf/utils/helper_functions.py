import inspect

import collections.abc as abc
from collections import (
    defaultdict,
    deque,
    namedtuple,
)

from functools import wraps

from restpf.utils.constants import (
    CallbackRegistrarOptions,
    TopologySearchColor,
)


def namedtuple_with_default(name, *pairs):
    keys, defaults = zip(*pairs)

    gencls = namedtuple(name, keys)
    gencls.__new__.__defaults__ = tuple(defaults)
    return gencls


def to_iterable(element):
    if not isinstance(element, abc.Iterable):
        element = (element,)
    return element


_extract_kwargs_subset_cache = {}


def _extract_kwargs_subset(func, kwargs):

    if func in _extract_kwargs_subset_cache:
        params_all, params_without_default = _extract_kwargs_subset_cache[func]

    else:
        sig_parameters = inspect.signature(func).parameters

        params_all = set(sig_parameters)
        params_without_default = set(filter(
            lambda k: sig_parameters[k].default is inspect.Parameter.empty,
            params_all,
        ))

        _extract_kwargs_subset_cache[func] = (
            params_all, params_without_default,
        )

    kwargs_keys = set(kwargs)

    if not params_without_default <= kwargs_keys:
        raise RuntimeError('Missing keys')

    intersection = params_all & kwargs_keys
    kwargs_subset = {key: kwargs[key] for key in intersection}

    return kwargs_subset


async def async_call(func, *args, **kwargs):
    if not args:
        # turn on kwargs filtering.
        kwargs = _extract_kwargs_subset(func, kwargs)

    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    else:
        return func(*args, **kwargs)


def bind_self_with_options(names, self, options):
    for name in names:
        setattr(self, name, options.get(name))


def method_named_args(*names):

    def _decorator(init):

        @wraps(init)
        def _wrapper(self, *args, **kwargs):
            bind_self_with_options(names, self, kwargs)
            # remove from kwargs.
            for name in names:
                kwargs.pop(name, None)
            # pass to original init.
            init(self, *args, **kwargs)

        return _wrapper

    return _decorator


def property_with_cache(accessor):

    CACHE_KEY = f'__cache_for_{accessor.__name__}'

    @wraps(accessor)
    def _wrapper(self):
        sp = super(type(self), self)

        try:
            _cache = sp.__getattribute__(CACHE_KEY)
        except AttributeError:
            _cache = None

        if _cache is None:
            _cache = accessor(self)
            sp.__setattr__(CACHE_KEY, _cache)

        return _cache

    return property(_wrapper)


# restricted options only contains CallbackRegistrarOptions.
def parallel_groups_of_callbacks(callback_and_restricted_options):
    root = None
    last = None

    children = defaultdict(set)
    parents = defaultdict(set)

    searched = {}
    search_starts = []

    # one pass processing.
    for callback, options in callback_and_restricted_options:

        before_all = options.get(CallbackRegistrarOptions.BEFORE_ALL.value)
        after_all = options.get(CallbackRegistrarOptions.AFTER_ALL.value)
        run_after = options.get(CallbackRegistrarOptions.RUN_AFTER.value)

        # check root.
        if before_all:
            if after_all or run_after:
                raise RuntimeError('Conflict on before_all.')
            if root is None:
                root = callback
                # to make sure search_starts not doesn't contain root.
                searched[callback] = TopologySearchColor.WHITE
                continue
            else:
                raise RuntimeError(
                    'Already set before_all: {}'.format(str(root)),
                )

        # check last.
        if after_all:
            if before_all:
                raise RuntimeError('Conflict on after_all.')
            if last is None:
                last = callback
                continue
            else:
                raise RuntimeError(
                    'Already set after_all: {}'.format(str(last)),
                )

        # check precedent.
        parent = run_after

        if inspect.isfunction(parent):
            children[parent].add(callback)
            parents[callback].add(parent)

        elif isinstance(parent, abc.Iterable):
            parents[callback] |= set(parent)

            for parent in parents[callback]:
                assert inspect.isfunction(parent)
                children[parent].add(callback)

        elif parent is None:
            search_starts.append(callback)

        else:
            raise NotImplemented

        # mark as not searched.
        searched[callback] = TopologySearchColor.WHITE

    # deal with root.
    if root:
        # binding.
        for child in search_starts:
            children[root].add(child)
            parents[child].add(root)
        # only one start point.
        search_starts = [root]

    def DFS(group, callback):
        # stop searching.
        if searched[callback] == TopologySearchColor.GRAY:
            raise RuntimeError('Detect circle.')
        if searched[callback] != TopologySearchColor.WHITE:
            return

        searched[callback] = TopologySearchColor.GRAY

        for child_callback in children.get(callback, []):
            DFS(group, child_callback)

        searched[callback] = TopologySearchColor.BLACK
        group.append(callback)

    # topology sort.
    groups = []

    for start_callback in search_starts:
        group = []
        DFS(group, start_callback)
        if group:
            groups.append(
                deque(reversed(group)),
            )

    parallel_groups = []
    while groups:
        # promise: each group is not empty.
        parallel_group = []

        for group in groups:
            while group:
                callback = group[0]

                ready = True
                for parent in parents[callback]:
                    if searched[parent] == TopologySearchColor.BLACK:
                        ready = False
                        break

                if ready:
                    parallel_group.append(callback)
                    group.popleft()
                else:
                    break

        # mark as completed.
        for callback in parallel_group:
            searched[callback] = TopologySearchColor.WHITE

        parallel_groups.append(parallel_group)
        groups = list(filter(bool, groups))

    if last:
        parallel_groups.append([last])

    return parallel_groups
