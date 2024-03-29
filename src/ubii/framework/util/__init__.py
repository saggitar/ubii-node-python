from __future__ import annotations

from codestare.async_utils import (
    accessor,
    condition_property,
    make_async,
    CoroutineWrapper,
    TaskNursery,
    async_exit_on_exc,
    awaitable_predicate,
    RegistryMeta,
    Registry,
)

from .collections import (
    merge_dicts,
    MatchMappingMixin,
    DefaultHookMap
)
from .enum import EnumMatcher
from .functools import (
    similar,
    hook,
    registry,
    exc_handler_decorator,
    calc_delta,
    log_call,
    ProtoRegistry,
    function_chain,
    compose,
    make_dict,
    async_compose,
    enrich,
    AbstractAnnotations,
    document_decorator,
    dunder
)

try:
    from functools import cached_property
except ImportError:
    from backports.cached_property import cached_property  # noqa

__DEBUG__ = False


def debug(enabled: bool | None = None) -> bool:
    """
    Call without arguments to get current debug state, pass truthy value to set debug mode.

    Args:
        enabled: If passed, turns debug mode on or off

    Returns:
        debug value
    """
    global __DEBUG__
    if enabled is not None:
        __DEBUG__ = bool(enabled)

    return __DEBUG__


__all__ = (
    "accessor",
    "awaitable_predicate",
    "condition_property",
    "make_async",
    "CoroutineWrapper",
    "TaskNursery",
    "async_exit_on_exc",
    "RegistryMeta",
    "Registry",
    "similar",
    "hook",
    "registry",
    "exc_handler_decorator",
    "log_call",
    "ProtoRegistry",
    "function_chain",
    "compose",
    "make_dict",
    "merge_dicts",
    "async_compose",
    "enrich",
    "calc_delta",
    "AbstractAnnotations",
    "debug",
    "document_decorator",
    "dunder"
)
