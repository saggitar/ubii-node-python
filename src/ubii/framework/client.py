"""
This module implements the framework to implement a Ubi Interact Node.
A Ubi Interact Node is expected to perform some communication with the `master node` and conceptualizes
the API for all *master node* interactions, e.g. subscribing to topics, registering devices and so on.
What exactly is expected from a client node to be functional, and what the specific client node actually
implements on top of the required behavior depends on the state of the *master node* implementation.

The currently used *master node* for all Ubi Interact scenarios is the `Node JS node`, which expects
the client node to:

    -   provide an API for all advertised service calls
    -   register itself
    -   establish a data connection to receive :class:`~ubii.proto.TopicData` messages for subscribed topics

Optionally, some client nodes (e.g. a Python Node using the :class:`ubii.node.protocol.DefaultProtocol` or the
`Node JS node` know how to communicate with the `master node` to e.g.

    -   start and stop `Processing Modules`


These two requirements are separated in the python framework:

1.  A :class:`.UbiiClient` defines what kind of behavior and features it is able to implement
2.  A :class:`.AbstractClientProtocol` provides a flexible framework to implement the necessary communication
    with the `master node` to implement those features

A :class:`.UbiiClient` only knows how to communicate with the `master node` through its
:attr:`~.UbiiClient.protocol` i.e. an existing :class:`.UbiiClient` should be able to handle
different `master node` versions by simply using a corresponding protocol if necessary.


A client node typically provides lots of different features, some could be methods (like subscribing
and unsubscribing from topics) or simply other objects that encapsulate different parts of a feature.
Instead of fixing the design, the framework uses :mod:`dataclasses` to describe an arbitrary number of
user defined attributes, grouped by feature:

    -   each :obj:`~dataclasses.dataclass` describes one feature
    -   the :class:`.UbiiClient` is initialized with lists of dataclasses for required and optional behaviors
    -   the attributes of the :obj:`~dataclasses.dataclass` are accessible through the :class:`.UbiiClient` (for
        every class passed during initialization)
    -   since :obj:`dataclasses <dataclasses.dataclass>` enforce the use of typehints, a
        :class:`.UbiiClient` provides a typed API even for its dynamically added attributes (the tradeoff being
        increased verbosity when accessing the attributes)
    -   each attribute will be "assigned" at some point during the execution of the clients
        :attr:`.UbiiClient.protocol`

Note:
    A client is considered usable if all attributes defined by *required* behaviors have been assigned!


By default, the :class:`.UbiiClient` has the required behaviors:
    -   :class:`.Services`
    -   :class:`.Subscriptions`
    -   :class:`.Publish`

and optional behaviors
    -   :class:`.Register`
    -   :class:`.Devices`
    -   :class:`.RunProcessingModules`
    -   :class:`InitProcessingModules`

The :class:`ubii.node.DefaultProtocol` will register the client and implement
the behaviors. The client is considered usable as soon as the required behaviors are implemented,
i.e. when it is able to make service calls, (un-)subscribe to/from topics and publish its own
:class:`ubii.proto.TopicData`.
Subscribing and unsubscribing are technically partly also service calls, but in addition to communicating the
intent to subscribe or unsubscribe to the master node, they return special :class:`~ubii.framework.topics.Topic`
objects, that can be used to handle to published :class:`~ubii.proto.TopicData`. This is explained in
greater detail in the :class:`~ubii.framework.topics.Topic` documentation.
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
import dataclasses
import logging
import typing
import warnings

import itertools
import ubii.proto

from . import (
    services,
    topics,
    util,
    constants,
    protocol, processing
)
from .util.functools import document_decorator
from .util.typing import (
    Protocol,
    T_EnumFlag,
    T,
    Decorator
)

__protobuf__ = ubii.proto.__protobuf__

log = logging.getLogger(__name__)

T_Protocol = typing.TypeVar('T_Protocol', bound='AbstractClientProtocol')

_data_kwargs = {'init': True, 'repr': True, 'eq': True}

BehaviorDict = typing.TypedDict(
    'BehaviorDict',
    {'required_behaviors': typing.Tuple[typing.Type, ...], 'optional_behaviors': typing.Tuple[typing.Type, ...]}
)


class subscribe_call(Protocol):
    patterns: typing.Tuple[str, ...]

    def with_callback(self, callback: topics.Consumer) -> (
            typing.Awaitable[typing.Tuple[typing.Tuple[topics.Topic, ...], typing.Tuple]]
    ):
        """
        set optional callback that should be registered for the subscribed topics

        Args:
            *callback: topic data consumer

        Returns:
            topic and tokens for callback de-registration
        """

    def __call__(self, *pattern: str) -> subscribe_call:
        """
        subscribe_call objects need to have this call signature
        needs to set :attr:`.patterns` attribute.

        Args:
            *pattern: unix wildcard patterns or absolute topic names
        """

    def __await__(self) -> typing.Generator[typing.Any, None, typing.Tuple[topics.Topic, ...]]:
        """
        Returns:
            a tuple of processed topics (one for each pattern, in :attr:`.patterns` same order)
        """


class unsubscribe_call(Protocol):

    def __call__(self, *pattern: str) -> typing.Awaitable[typing.Tuple[topics.Topic, ...]]:
        """
        unsubscribe_call objects need to have this call signature

        Args:
            *pattern: unix wildcard patterns or absolute topic names

        Returns:
            awaitable returning a tuple of processed topics (one for each pattern, same order)
        """


class publish_call(Protocol):
    def __call__(self, *records: ubii.proto.TopicDataRecord | typing.Dict) -> typing.Awaitable[None]:
        """
        publish_call objects need to have this call signature

        Args:
            *records: :class:`~ubii.proto.TopicDataRecord` messages or compatible dictionaries

        Returns:
            some awaitable performing the `master node` communication
        """


class start_session(Protocol):
    async def __call__(self, session: ubii.proto.Session) -> ubii.proto.Session:
        """
        Await to start a session
        Args:
            session: session request to start

        Returns:
            the started session specifications
        """


class stop_session(Protocol):
    async def __call__(self, session: ubii.proto.Session) -> bool:
        """
        Await to stop a session
        Args:
            session: session request to stop

        Returns:
            wether stopping was successful
        """


@dataclasses.dataclass(**_data_kwargs)
class Services:
    """
    Behavior to make service calls (accessed via the service map)

    Example:

        >>> from ubii.node import *
        >>> client = await connect_client()
        >>> assert client.implements(Services)
        >>> await client[Services].service_map.server_config()
        server {
          id: "c2741cca-c75a-41d4-820a-80ac6407d791"
          name: "master-node"
          [...]
        }

    """
    service_map: services.DefaultServiceMap | None = None
    """
    The :class:`~.services.DefaultServiceMap` can be accessed with "shortcuts" for service topics
    
    See Also: 
        :attr:`.services.DefaultServiceMap.defaults` -- how attribute access for the service map works
    """


@dataclasses.dataclass(**_data_kwargs)
class Subscriptions:
    """
    Behavior to subscribe and unsubscribe from topics

    Example:

        >>> from ubii.node import *
        >>> client = await connect_client()
        >>> start_pm, = await client[Subscriptions].subscribe_topic('/info/processing_module/start')
        >>> start_pm.subscriber_count
        1

    """
    subscribe_regex: subscribe_call | None = None
    """
    await to subscribe with regex
    """
    subscribe_topic: subscribe_call | None = None
    """
    await to subscribe with simple topic
    """
    unsubscribe_regex: unsubscribe_call | None = None
    """
    await to unsubscribe with regex
    """
    unsubscribe_topic: unsubscribe_call | None = None
    """
    await to unsubscribe with simple topic
    """


@dataclasses.dataclass(**_data_kwargs)
class Publish:
    """
    Behavior to publish :class:`ubii.proto.TopicDataRecord` messages.
    If multiple records are passed they should be converted to a :class:`ubii.proto.TopicDataList` and published as such,
    otherwise they should be wrapped in a :class:`ubii.proto.TopicData` message.
    """
    publish: publish_call | None = None
    """
    await to publish topic data
    """


@dataclasses.dataclass(**_data_kwargs)
class Register:
    """
    Behavior to optionally unregister and re-register the client node (registering once is probably required
    to establish a data connection for :class:`.Publish` behavior but unregistering and re-registering is typically
    optional -- consult the documentation of the used :class:`protocol <.AbstractClientProtocol>` for details)
    """
    register: typing.Callable[[], typing.Awaitable[UbiiClient]] | None = None
    """
    await to register client node
    """
    deregister: typing.Callable[[], typing.Awaitable[bool | None]] | None = None
    """
    await to unregister client node
    """


@dataclasses.dataclass(**_data_kwargs)
class Devices:
    """
    Behavior to register and deregister Devices (optional)
    """
    register_device: typing.Callable[[ubii.proto.Device], typing.Awaitable[ubii.proto.Device]] | None = None
    """
    await to register a device
    """
    deregister_device: typing.Callable[[ubii.proto.Device], typing.Awaitable[None]] | None = None
    """
    await to deregister a device
    """


@dataclasses.dataclass(**_data_kwargs)
class Sessions:
    """
    Behavior to start and stop Sessions
    """
    sessions: typing.Dict[str, ubii.proto.Session] | None = None
    """
    the sessions started by this node
    """
    start_session: start_session | None = None
    """
    await to start a session 
    """
    stop_session: stop_session | None = None
    """
    await to stop a session 
    """
    get_sessions: typing.Callable[[], typing.Awaitable[ubii.proto.SessionList]] | None = None
    """
    await to get running sessions from broker
    """


class wait_for_module(Protocol):
    def __call__(
            self, name: str,
            *possible_status: ubii.proto.ProcessingModule.Status
    ) -> typing.Awaitable[processing.ProcessingRoutine]:
        """
        Wait until the specified module has the specified status

        Args:
            name: module name
            possible_status: callable should use INITIALIZED by default if no other status is given,
                any statuses given will be accepted

        Returns:
            an awaitable returning the module once it has (one of) the specified status(es)
        """


@dataclasses.dataclass(**_data_kwargs)
class RunProcessingModules:
    """
    Access all running processing module instances
    """
    get_module_instance: wait_for_module | None = None
    """
    Wait until the specified module is e.g. initialized, then return the module
    """


ProcessingModuleFactory = typing.Callable[..., processing.ProcessingRoutine]
"""
Convenience Type
"""


@dataclasses.dataclass(**_data_kwargs)
class InitProcessingModules:
    """
    Behavior to initialize ProcessingModules with custom callables
    """
    module_factories: typing.Mapping[str, ProcessingModuleFactory] | None = None
    """
    Mapping :math:`name \\rightarrow factory` for module names to 
    callables which return a :class:`processing.ProcessingRoutine` instance. If the client
    implements it, you can put custom callables inside, so they will get used during module
    instantiation
    """


@dataclasses.dataclass(**_data_kwargs)
class DiscoverProcessingModules:
    """
    Behaviour to automatically load ProcessingModules
    """
    discover_processing_modules: typing.Callable[[], typing.Dict[str, ProcessingModuleFactory]] | None = None
    """
    Callable returning a mapping of math:`name \\rightarrow factory` for module names to 
    callables which return a :class:`processing.ProcessingRoutine` instance.
    """


@util.dunder.repr('id')
class UbiiClient(ubii.proto.Client,
                 typing.Awaitable['UbiiClient'],
                 typing.Generic[T_Protocol],
                 metaclass=util.ProtoRegistry):
    """
    A :class:`UbiiClient` inherits its proto message wrapping capabilities from :class:`ubii.proto.Client`.

    The protocol of the client typically implements the following additional behaviors:

        *   making :class:`ServiceCalls <.services.ServiceCall>` via the :class:`Services` behavior --
            this involves accessing the right Service for your task by topic, and calling it with the right kind of
            data (see https://github.com/SandroWeber/ubi-interact/wiki/Requests for more documentation on default
            topics for services and expected data)

        *   subscribe to topics (or topic patterns) at the master node -- this process involves making the right
            service call and then creating a internal representation of the topic to add callbacks and forward
            received data. Because of this complexity you should not subscribe to topics via a simple `ServiceCall`,
            and instead use the :class:`Subscriptions` behavior. Make sure to use the ``_regex`` version of a method
            when you subscribe to a wildcard pattern.

        *   publish data on topics -- this requires a :class:`~ubii.proto.TopicDataRecord` message or
            a compatible dictionary (see documentation of the message formats) and the :class:`Publish` behavior

        *   run `Processing Modules` -- processing modules need to be registered at the master node.
            Add the modules to the :attr:`~.UbiiClient.processing_modules` field of the client for PMs which can be
            initialized when the client node is created, or to the
            :class:`~InitProcessingModules.late_init_processing_modules` field of the :class:`InitProcessingModules`
            behavior for modules that need to be initialized at a later point of the protocol (e.g. a processing
            module might need to know the *master node's* definition of datatype messages, so it can only be initialized
            after some initial communication between `client` and `master node`.

    The :class:`UbiiClient` will start it's :class:`Client Protocol <AbstractClientProtocol>` when it is awaited
    directly or indirectly (see examples below). The protocol will implement the `behaviors`.

    It's required to link a client and its protocol explicitly::

        from ubii.node.protocol import DefaultProtocol
        from ubii.framework.client import UbiiClient, Services
        import asyncio

        async def main():
            protocol = DefaultProtocol()
            client = UbiiClient(protocol=protocol)
            protocol.client = client

            ...

        asyncio.run(main())

    Awaiting a :class:`UbiiClient` object::

        from ubii.node.protocol import DefaultProtocol
        from ubii.framework.client import UbiiClient, Services
        import asyncio

        async def main():
            protocol = DefaultProtocol()
            client = UbiiClient(protocol=protocol, name="Foo")  # name is a message field
            protocol.client = client
            assert client.name == 'Foo'

            # you could set some attributes before you 'start' the client
            client.is_dedicated_processing_node = True

            # now wait for the client to be usable
            client = await client
            assert client.id  # will be set because the client is registered now

    Using the :class:`UbiiClient` object as an async context manager::

        from ubii.node.protocol import DefaultProtocol
        from ubii.framework.client import UbiiClient, Services
        import asyncio

        async def main():
            protocol = DefaultProtocol()
            client = UbiiClient(protocol=protocol, name="Foo")  # name is a message field
            protocol.client = client

            async with client as running:
                assert running.id  # client is already registered

            assert not client.id  # client gets unregistered when context exits

    When the client is awaited (either directly or as an async context manager) the protocol is started
    internally unless it is already running. Refer to :meth:`.AbstractClientProtocol.start` for details.

    Attributes:
        registry (Dict[str, UbiiClient]): Mapping :math:`id \\rightarrow Client` containing all live :class:`UbiiClients <UbiiClient>`
            with id. Refer to the documentation of :class:`util.ProtoRegistry` for details. ::

                from ubii.framework.client import UbiiClient
                from ubii.node.protocol import DefaultProtocol

                async def main():
                    # you could instead use ubii.node.connect_client
                    protocol = DefaultProtocol()
                    client = UbiiClient(protocol=protocol)
                    protocol.client = client

                    # empty dictionary, since client does not have an id
                    assert not client.id
                    assert not UbiiClient.registry

                    # starts client protocol and returns control when client has id
                    await client

                    assert client.id
                    assert UbiiClient.registry[client.id] == client
    """

    __unique_key_attr__: str = 'id'

    IMPLEMENT_TIMEOUT = None
    """
    Set this value if you want to debug code that hangs in waiting for implementations using
    :attr:`.implements`
    """

    @util.dunder.repr('client')
    class ClientInitTaskWrapper(typing.Awaitable['UbiiClient']):
        """
        This is a wrapper around a task that waits until the client implements the required behaviors,
        and then returns the client. The wrapper can be `reset`, with :attr:`.reset` so that a new
        task is created to be used inside the wrapper.
        """

        def __init__(self, client: UbiiClient):
            self.client = client
            self._task: asyncio.Task | None = None
            self._set_task()

        def _set_task(self):
            self._task = self.client.task_nursery.create_task(self._wait_for_client_implementation())

        def reset(self) -> None:
            """
            Use this method to reset the client behaviors and create a new wrapped task inside the wrapper.

            Returns:
                Reference to self, with new wrapped task

            """
            if not self.client.protocol.finished:
                raise ValueError(
                    f"Can't reset client protocol {self.client}, "
                    f"protocol is not in end state {self.client.protocol.end_state!r}, is in "
                    f"{self.client.protocol.state.value!r} instead."
                )

            for behavior in self.client._behaviors:
                for field in dataclasses.fields(behavior):
                    setattr(self.client[behavior], field.name, None)

            self._set_task()

        async def _wait_for_client_implementation(self):
            await self.client.implements(*self.client._required_behaviors)
            return self.client

        def __await__(self):
            return self._task.__await__()

    def __init__(self: UbiiClient[T_Protocol],
                 mapping=None, *,
                 protocol: T_Protocol,
                 required_behaviors: typing.Tuple[typing.Type, ...] = (
                         Services, Subscriptions, Publish
                 ),
                 optional_behaviors: typing.Tuple[typing.Type, ...] = (
                         Register,
                         Devices,
                         RunProcessingModules,
                         InitProcessingModules,
                         DiscoverProcessingModules,
                         Sessions,
                 ),
                 **kwargs):
        """
        Creates a :class:`UbiiClient` object.
        The :class:`UbiiClient` is awaitable. When it is used in an :ref:`await`, the coroutine will
        wait until all attributes for the clients `required_behaviors` are assigned. These assignments
        typically happen as part of the clients :attr:`.protocol` running, sometime the types
        passed as `required_behaviors` or `optional_behaviors` are referred to as
        `behaviors`, and assigning something to their attributes is referred to as `implementing` the behavior.


        Args:
            mapping (Union[dict, ~.Message]): A dictionary or message to be
                used to determine the values for the message fields.
            protocol (AbstractClientProtocol): A concrete protocol instance to be used py the client node
            required_behaviors (typing.Tuple[typing.Type, ...]): tuple of :obj:`~dataclasses.dataclass` types
                that need to be `implemented` by the protocol to consider the `UbiiClient` as usable
            optional_behaviors (typing.Tuple[typing.Type, ...]): tuple of :obj:`~dataclasses.dataclass` types
                that can optionally be `implemented` by the protocol whose attributes can be accessed through the
                `UbiiClient` node.
            **kwargs: passed to :class:`ubii.proto.Client` (e.g. field assignments)
        """
        super().__init__(mapping=mapping, **kwargs)

        self._required_behaviors = required_behaviors or ()
        self._optional_behaviors = optional_behaviors or ()
        behaviors = list(
            itertools.chain(self._required_behaviors, self._optional_behaviors)
        )

        if not all(dataclasses.is_dataclass(b) for b in behaviors):
            raise ValueError(f"Only dataclasses can be passed as behaviors")

        if not self.name:
            self.name = f"Python-Client-{self.__class__.__name__}"  # type: str

        if 'state' not in self:
            self.state = self.State.UNAVAILABLE

        self._change_specs = asyncio.Condition()
        self._notifier = None
        self._protocol = protocol
        self._behaviors = {kls: self._patch_behavior(kls)() for kls in behaviors}

        self._ctx: typing.AsyncContextManager = self._with_running_protocol()
        self._init = self.ClientInitTaskWrapper(self)
        self._init_specs: typing.Dict = type(self).to_dict(self)

    @property
    def initial_specs(self) -> dict:
        """
        Since clients can be reset, the clients current representation needs to be
        separated from the initial protobuf specifications. When the client is :func:`.reset`,
        it's specifications will be set to it's current :attr:`.initial_specs`.

        The initial specs can be adapted during the client's lifetime by
        explicitly assigning to values of this dictionary, otherwise it contains the specifications
        that were used when the object was initialized.
        """
        return self._init_specs

    def _patch_behavior(self, behavior: typing.Type):
        """
        Setting attributes of the behavior should notify the tasks waiting for changed specs of this client,
        e.g. the tasks waiting for implementation state.
        """
        client = self

        # see if (https://github.com/python/mypy/issues/5865) is resolved to check if mypy gets this
        class _(behavior):  # type: ignore
            """
            Proxy to notify client of changed fields.
            """

            def __setattr__(self, key, value):
                super().__setattr__(key, value)
                client.notify()

            def __repr__(self):
                fields = dataclasses.fields(self)
                return (f"{behavior.__module__}.{behavior.__name__}"
                        f"({', '.join('{}={!r}'.format(f.name, getattr(self, f.name)) for f in fields)})")

        from .util.functools import append_doc
        append_doc(_)(behavior.__doc__)
        return _

    def notify(self) -> None:
        """
        Creates a task to notify all coroutines waiting for :attr:`.change_specs` (allows easy notification
        from outside a coroutine i.e. a non-async callback, where it's impossible to acquire the :attr:`.change_specs`
        lock asynchronously)
        """
        assert self.protocol
        assert self._change_specs
        assert hasattr(self, '_notifier')

        async def _notify():
            async with self._change_specs:
                self._change_specs.notify_all()
                self._notifier = None

        if not self._notifier:
            self._notifier = self.task_nursery.create_task(_notify())

    @property
    def task_nursery(self) -> util.TaskNursery:
        """
        the :class:`~codestare.async_utils.nursery.TaskNursery` used by the :attr:`.protocol`
        """
        return self.protocol.task_nursery

    @property
    def change_specs(self) -> asyncio.Condition:
        """
        Allows waiting for behavior attribute assignments.
        See also: :attr:`.implements`
        ::

            from ubii.node import connect_client

            # we use connect_client to create a UbiiClient as well as a protocol and connect them
            # see documentation of connect_client for details

            async def main():
                async with connect_client() as client:
                    await client.change_specs.wait()
                    print("A behavior was implemented!")

        """
        return self._change_specs

    def implements(
            self,
            *behaviors,
            timeout: float | None = IMPLEMENT_TIMEOUT,
    ) -> util.awaitable_predicate:
        """
        Returns an object that can be used to check if the client implements a certain behavior
        or wait until it is implemented.

        ::

            from ubii.node.protocol import DefaultProtocol
            from ubii.framework.client import UbiiClient, Services
            import asyncio

            async def main():
                protocol = DefaultProtocol()
                client = UbiiClient(protocol=protocol)
                protocol.client = client

                async def wait_for_required_behaviors_implicitly():
                    return await client

                async def wait_for_behavior_explicitly():
                    await client.implements(Services)  # used in await expression
                    assert client.implements(Services)  # used in boolean expression

                await asyncio.gather(
                    wait_for_required_behaviors_implicitly(),
                    wait_for_behavior_explicitly()
                )


            asyncio.run(main())

        Args:
            *behaviors: tuple of :obj:`~dataclasses.dataclass` types passed to
                this :class:`UbiiClient` as `required_behaviors` or `optional_behaviors` during initialization.

            timeout: if not None, the returned awaitable will raise a :class:`asyncio.TimeoutError` after specified time
        Returns:
            an :class:`~ubii.framework.util.awaitable_predicate` that converts to
            `True` if all fields of the passed `behaviors` are initialized in this :class:`UbiiClient`
            and / or can be used in an :ref:`await` to wait until that is the case
        """

        def fields_not_none():
            return all(getattr(self._behaviors[b], field.name) is not None
                       for b in behaviors for field in dataclasses.fields(b))

        return util.awaitable_predicate(predicate=fields_not_none, condition=self._change_specs, timeout=timeout)

    @property
    def behaviors(self) -> BehaviorDict:
        """
        Return mapping :math:`(optional / required) \\rightarrow dataclass` showing which behaviors are defined as
        optional or required by this client. You can check their implementation status using :func:`.implements`.
        """
        return {'optional_behaviors': self._optional_behaviors, 'required_behaviors': self._required_behaviors}

    def wants(self, *behaviors) -> bool:
        """
        Checks if the passed behaviours are part of the clients required or optional behaviours and are not implemented
        Basically just a shorthand for ::

            all(
                (behavior in self.behaviors['optional_behaviors'] or behavior in self.behaviors['required_behavior']
                for behavior in behaviors
            )

        Args:
            *behaviours: behavior types to check

        Returns:
            True if all behaviors are contained in required or optional behaviors
        """
        return all(
            behavior in self.behaviors['required_behaviors'] or behavior in self.behaviors['optional_behaviors']
            for behavior in behaviors
        )

    @contextlib.asynccontextmanager
    async def _with_running_protocol(self):
        async with self.protocol:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                client = await self
            yield client

    def __await__(self):
        if self.protocol.finished:
            warnings.warn(f"{self} was stopped, you can "
                          f"try triggering a protocol restart by resetting the "
                          f"client with client.reset()", UserWarning)

        if not self.protocol.was_started:
            self.protocol.start()

        return self._init.__await__()

    def __aenter__(self):
        return self._ctx.__aenter__()

    def __aexit__(self, *exc_info):
        return self._ctx.__aexit__(*exc_info)

    async def reset(self):
        """
        Use this method to reset the client behaviors and allow explicitly restarting the client protocol
        if it is finished. Also resets the protobuf values to the contents of :attr:`.initial_values`

        Warning:

            This behavior is experimental, it is better to simply create a new client instance
        """
        old_id = self.id
        if hasattr(self.task_nursery, 'sentinel_task') and not self.task_nursery.sentinel_task:
            log.debug(f"{self}'s task nursery needs seems to be dead."
                      f" Creating new task nursery for {self}.")

            stack = self.task_nursery.pop_all()
            self.protocol.task_nursery = type(self.task_nursery)(
                name=self.task_nursery.name,
                loop=self.task_nursery.loop
            )
            await self.task_nursery.enter_async_context(stack)

        self._init.reset()

        warning = None

        try:
            await self.protocol.state.set(None)
        except ValueError:
            warning = (f"{type(self.protocol)} does not seem to support resetting the protocol state."
                       f" It does not define a state change from its end state to None!")

        for name, value in self._init_specs.items():
            setattr(self, name, value)

        if warning:
            warnings.warn(warning)
        else:
            log.debug(f"{self} (with old id {old_id!r}) was reset successfully and can be used again.")

    @property
    def protocol(self: UbiiClient[T_Protocol]) -> T_Protocol:
        """
        Reference to protocol used by the client
        """
        return self._protocol

    def __getitem__(self, behavior: typing.Type[T]) -> T:
        return self._behaviors[behavior]

    def __setitem__(self, key, value):
        if not dataclasses.is_dataclass(value):
            raise ValueError(f"can only assign dataclass instances to {key}, got {type(value)}")

        # create copy of value that is a proxy object
        self._behaviors[key] = self._patch_behavior(type(value))(**dataclasses.asdict(value))  # type: ignore
        self.notify()


@util.dunder.all('client')
class AbstractClientProtocol(protocol.AbstractProtocol[T_EnumFlag], util.Registry, abc.ABC):
    """
    :class:`~abc.ABC` to implement client protocols, i.e. define the communication between
    `client node` and `master node` during the lifetime of the client.

    Attributes:
        state_changes: inherited from :class:`~ubii.framework.protocol.AbstractProtocol`
    """
    hook_function: util.registry[str, util.hook] = util.registry(key=lambda h: h.__name__, fn=util.hook)
    """
    This callable wraps the :class:`util.hook <ubii.framework.util.functools.hook>` 
    decorator but registers every decorated function, so that decorators can be easily applied to all registered hooks
    simultaneously
    """
    __hook_decorators__: typing.Set[Decorator] = set()

    @property
    def __registry_key__(self):
        """
        A Standard Protocol can ba associated with _one_ client, so if we have a registered client with
        unique id, we use this id as the key for the registry view.
        During initialisation of the client the key is the default value (~ __module__.__qualname__.#id)
        """
        default = type(self).__default_key_value__
        if not hasattr(self.context, 'client') or not self.context.client or not self.context.client.id:
            return default

        return self.context.client.id

    def __init__(self, config: constants.UbiiConfig = constants.GLOBAL_CONFIG, log: logging.Logger | None = None):
        self.log = log or logging.getLogger(__name__)
        self.config: constants.UbiiConfig = config
        """
        Config used -- contains e.g. default topic for initial `server configuration` service call
        """
        super().__init__()

    @abc.abstractmethod
    async def create_service_map(self, context):
        """
        Create a :class:`~ubii.framework.services.ServiceMap` in the context as
        ``context.service_map`` which has to be able to make a single service call ``context.service_map.server_config``
        """

    @abc.abstractmethod
    async def update_config(self, context):
        """
        Update the server configuration in the context. After completion of this coroutine

            *   ``context.server`` is a :class:`~ubii.proto.Server` message with the configuration of the master node
            *   ``context.constants``  is a :class:`~ubii.proto.Constants` message of the default constants of the
                master node
        """

    @abc.abstractmethod
    async def update_services(self, context):
        """
        Update the service map in the context.

            *   ``context.service_map`` is able to perform all service calls advertised by the master node
                after this coroutine completes.
        """

    @abc.abstractmethod
    async def create_client(self, context):
        """
        Create a client in the context.

            *   ``context.client`` typically is a :class:`ubii.proto.Client` wrapper, e.g. a :class:`UbiiClient`
                which at this moment is not expected to be fully functional.
        """

    @abc.abstractmethod
    def register_client(self, context) -> typing.AsyncContextManager[None]:
        """
        Return a context manager to register the ``context.client`` client, and unregister it when the protocol stops.
        After successful registration the context manager typically needs to also set the protocol state to whatever
        the concrete implementation expects.

            *   ``context.client`` is expected to be up-to-date and usable after registration
        """

    @abc.abstractmethod
    async def create_topic_connection(self, context):
        """
        Should create a :class:`ubii.framework.topics.DataConnection`.

            *   ``context.topic_connection`` is expected to be a fully functional topic connection after
                this coroutine is completed.
        """

    @abc.abstractmethod
    async def implement_client(self, context):
        """
        Make sure the ``context.client`` has fully implemented behavior. The context at this point should contain
        a `context.service_map` and a `context.topic_connection`.

            *   ``context.client`` can be awaited after this coroutine is finished, to return a fully functional client.
        """

    @hook_function
    @document_decorator('.hook_function')
    async def on_start(self, context: typing.Any) -> None:
        """
        Awaits (in order):
            - :meth:`create_service_map`
            - :meth:`update_config`
            - :meth:`update_services`
            - :meth:`create_client`

        The ``context`` is passed for each call, and updated according to the concrete implementation.

        Note:

            For a concrete implementation of a client protocol, assign this callback to a state change in
            :attr:`~ubii.framework.client.AbstractClientProtocol.state_changes`

        Args:
            context: A namespace or dataclass or similar object as container for manipulated values
        """
        await self.create_service_map(context)
        await self.update_config(context)
        await self.update_services(context)
        await self.create_client(context)

    @hook_function
    @document_decorator('.hook_function')
    async def on_create(self, context) -> None:
        """
        Enters the async context manager created by :meth:`register_client` in the :attr:`task_nursery` i.e.
        registers the client and prepares to unregister it if the protocol should be stopped

        The ``context`` is passed to :meth:`register_client`

        Note:
            For a concrete implementation of a client protocol, assign this callback to a state change in
            :attr:`~ubii.framework.client.AbstractClientProtocol.state_changes`

        Args:
            context: A namespace or dataclass or similar object as container for manipulated values
        """
        await self.task_nursery.enter_async_context(self.register_client(context))

    @hook_function
    @document_decorator('.hook_function')
    async def on_registration(self, context) -> None:
        """
        Awaits (in order):
            - :meth:`create_topic_connection`
            - :meth:`implement_client`

        Then the ``context.client`` is awaited to make sure that all behaviors are implemented.
        The ``context`` is passed for each call, and updated according to the concrete implementation.

        Note:

            For a concrete implementation of a client protocol, assign this callback to a state change in
            :attr:`~ubii.framework.client.AbstractClientProtocol.state_changes`

        Args:
            context: A namespace or dataclass or similar object as container for manipulated values

        Raises:
            RuntimeError: if awaiting the ``context.client`` raises a :class:`asyncio.TimeoutError` after a timeout of
                :math:`5s`
        """
        await self.create_topic_connection(context)
        await self.implement_client(context)
        try:
            # make sure client is implemented
            context.client = await asyncio.wait_for(context.client, timeout=5)
        except asyncio.TimeoutError as e:
            raise RuntimeError(f"Client is not implemented") from e

        self.client.state = self.client.State.ACTIVE

    @hook_function
    @document_decorator('.hook_function')
    async def on_connect(self, context) -> None:
        """
        Starts a :class:`ubii.framework.topics.StreamSplitRoutine` in the :attr:`task_nursery` to split
        :class:`ùbii.proto.TopicData` messages from the ``context.topic_connection`` to the topics of the
        ``context.topic_store``

        Note:
            For a concrete implementation of a client protocol, assign this callback to a state change in
            :attr:`~ubii.framework.client.AbstractClientProtocol.state_changes`

        Args:
            context: A namespace or dataclass or similar object as container for manipulated values
        """
        self.task_nursery.create_task(
            topics.StreamSplitRoutine(container=context.topic_store, stream=context.topic_connection)
        )

    @hook_function
    @document_decorator('.hook_function')
    async def on_stop(self, context) -> None:
        """
        Sets the :attr:`~UbiiClient.state` of the :attr:`client` to :attr:`~UbiiClient.State.UNAVAILABLE`

        Note:
            For a concrete implementation of a client protocol, assign this callback to a state change in
            :attr:`~ubii.framework.client.AbstractClientProtocol.state_changes`

        Args:
            context: A namespace or dataclass or similar object as container for manipulated values
        """

        self.log.info(f"Stopped protocol {self}")
        self.client.state = self.client.State.UNAVAILABLE

    def __init_subclass__(cls):
        """
        Register decorators for hook functions
        """
        hook_function: util.hook
        for hook_function, hk in itertools.product(cls.hook_function.registry.values(), cls.__hook_decorators__):
            if hk not in hook_function.decorators():
                hook_function.register_decorator(hk)

        super().__init_subclass__()
