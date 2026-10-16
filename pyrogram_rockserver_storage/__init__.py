__author__ = 'Andrea Cavalli'
__version__ = '0.6.1'

import asyncio
import json
import logging
import math
import random
import time
from enum import Enum
from dataclasses import dataclass
from itertools import chain
from string import digits
from typing import Any, List, Tuple, Dict, Optional, cast, Type, Generic, Callable, Awaitable, TypeVar

import grpc.aio
from grpc import Channel
from pyrogram import raw, utils
from pyrogram.storage import Storage

try:
    from pyrogram.storage import UpdateState
except ImportError:
    # The still-supported older SDK exposes tuple-based update_state only.
    @dataclass(frozen=True)
    class UpdateState:
        id: int
        pts: Optional[int]
        qts: Optional[int]
        date: Optional[int]
        seq: Optional[int]

from lru import LRU

import bson

import pyrogram_rockserver_storage.rocksdb_pb2 as rockserver_storage_pb2
from pyrogram_rockserver_storage.rocksdb_pb2_grpc import RocksDBServiceStub

SESSION_KEY = [bytes([0])]
DIGITS = set(digits)
TEST_DC_ADDRESSES = {
    1: "149.154.175.10",
    2: "149.154.167.40",
    3: "149.154.175.117",
}
PROD_DC_ADDRESSES = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
    203: "91.105.192.100",
}


# This TypeVar allows us to make the client generic.
# It can be any class that has gRPC methods.
StubType = TypeVar("StubType")

class ResilientRpcClient(Generic[StubType]):
    """Bounded unary RPC retries on a channel owned until explicit close.

    gRPC reconnects the channel itself. Replacing it after one failed RPC
    cancels unrelated calls, which can kill their caller's background tasks.
    """
    _CONTEXT_PROFILES = {
        "createColumn": (rockserver_storage_pb2.CreateColumnRequest, rockserver_storage_pb2.BATCH),
        "deleteColumn": (rockserver_storage_pb2.DeleteColumnRequest, rockserver_storage_pb2.BATCH),
        "get": (rockserver_storage_pb2.GetRequest, rockserver_storage_pb2.LATENCY),
        "put": (rockserver_storage_pb2.PutRequest, rockserver_storage_pb2.INGEST),
        "putMultiList": (rockserver_storage_pb2.PutMultiListRequest, rockserver_storage_pb2.INGEST),
    }

    _RECONNECTABLE_STATUS_CODES = {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.CANCELLED,
    }

    def __init__(self, hostname: str, port: int, stub_class: Type[StubType],
                 channel_options: Optional[list] = None,
                 compression: Optional[grpc.Compression] = grpc.Compression.Gzip,
                 retry_attempts: int = 3, initial_backoff_ms: int = 100,
                 max_backoff_ms: int = 5000, rpc_timeout: float = 30.0,
                 observer: Optional[Callable[[str, float], None]] = None):
        if retry_attempts < 1 or not math.isfinite(rpc_timeout) or rpc_timeout <= 0:
            raise ValueError("retry_attempts must be positive and rpc_timeout finite and positive")
        self._observer = observer
        self._hostname = hostname
        self._port = port
        self._stub_class = stub_class
        self._channel_options = channel_options
        self._compression = compression
        self._retry_attempts = retry_attempts
        self._initial_backoff_ms = initial_backoff_ms
        self._max_backoff_ms = max_backoff_ms
        self._rpc_timeout = rpc_timeout
        self._is_closing = False
        self._channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[StubType] = None
        self._lock = asyncio.Lock()

    def _observe(self, event: str, value: float = 1.0) -> None:
        """Optional local counter hook. Must not perform I/O or block.

        Observer failures cannot change RPC results or cancellation. The hook
        receives only fixed event names and numbers, never request information.
        """
        if self._observer is not None:
            try:
                self._observer(event, value)
            except (Exception, asyncio.CancelledError):
                pass

    def _observe_readiness(self) -> None:
        # Sampling is telemetry too: neither an unavailable probe nor an
        # observer failure may prevent the actual RPC. Avoid the probe entirely
        # when telemetry is disabled.
        if self._observer is not None:
            try:
                self._observe("ready_attempts" if self.is_connected else "not_ready_attempts")
            except (Exception, asyncio.CancelledError):
                pass

    @property
    def is_connected(self) -> bool:
        """Whether the transport is currently ready, not merely allocated."""
        return (self._channel is not None and
                self._channel.get_state() == grpc.ChannelConnectivity.READY)

    async def connect(self) -> None:
        """Allocate the transport; each RPC waits for readiness within its deadline."""
        async with self._lock:
            if self._is_closing:
                raise ConnectionError("gRPC client is closed")
            if self._stub is None:
                self._channel = grpc.aio.insecure_channel(
                    target=f'{self._hostname}:{self._port}',
                    compression=self._compression, options=self._channel_options)
                self._stub = self._stub_class(self._channel)
                self._observe("channels_created")
                logging.info("Created gRPC channel for %s:%s", self._hostname, self._port)

    async def close(self) -> None:
        async with self._lock:
            self._is_closing = True
            channel = self._channel
            self._channel = None
            self._stub = None
            if channel is not None:
                await channel.close()
                self._observe("channels_closed")

    @staticmethod
    def _deadline_error():
        return grpc.aio.AioRpcError(
            grpc.StatusCode.DEADLINE_EXCEEDED, (), (),
            details="RPC deadline exceeded including retries")

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        if name.startswith('_'):
            raise AttributeError(name)

        async def invoke_with_deadline(*args, **kwargs):
            timeout = kwargs.pop("timeout", None)
            timeout = self._rpc_timeout if timeout is None else timeout
            if not math.isfinite(timeout):
                raise ValueError("timeout must be finite")
            if timeout <= 0:
                raise self._deadline_error()
            deadline = time.monotonic() + timeout
            request_context = None
            context_spec = self._CONTEXT_PROFILES.get(name)
            request = args[0] if args else kwargs.get("request")
            if context_spec is not None and isinstance(request, context_spec[0]):
                copied_request = type(request)()
                copied_request.CopyFrom(request)
                request_context = (copied_request.initialRequest.context if name == "putMultiList"
                                   else copied_request.context)
                request_context.profile = context_spec[1]
                request_context.workloadContractVersion = 3
                if args:
                    args = (copied_request, *args[1:])
                else:
                    kwargs["request"] = copied_request

            async def invoke():
                connect_started = time.monotonic()
                try:
                    await self.connect()
                finally:
                    # Allocation/lock wait, not gRPC transport readiness latency.
                    self._observe("connect_wait_seconds", time.monotonic() - connect_started)
                backoff = self._initial_backoff_ms
                for attempt in range(self._retry_attempts):
                    if self._is_closing:
                        raise ConnectionError("gRPC client is closed")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise self._deadline_error()
                    if request_context is not None:
                        request_context.timeoutNanos = (9223372036854775806
                                                       if remaining >= 9223372036854775806 / 1_000_000_000
                                                       else max(1, int(remaining * 1_000_000_000)))
                    self._observe("attempts")
                    self._observe_readiness()
                    try:
                        return await getattr(self._stub, name)(
                            *args, timeout=remaining,
                            **{"wait_for_ready": True, **kwargs})
                    except grpc.aio.AioRpcError as error:
                        if (self._is_closing or
                                error.code() not in self._RECONNECTABLE_STATUS_CODES or
                                attempt == self._retry_attempts - 1):
                            raise
                        logging.warning("gRPC call %s failed with %s; retrying",
                                        name, error.code())
                    self._observe("retries")
                    retry_started = time.monotonic()
                    try:
                        await asyncio.sleep((backoff + random.randint(0, 50)) / 1000)
                    finally:
                        self._observe("retry_wait_seconds", time.monotonic() - retry_started)
                    backoff = min(self._max_backoff_ms, backoff * 2)

            # The budget also covers connection lock acquisition and retry sleeps.
            try:
                return await asyncio.wait_for(invoke(), max(0, deadline - time.monotonic()))
            except asyncio.TimeoutError as error:
                raise self._deadline_error() from error

        async def rpc_method_wrapper(*args, **kwargs):
            started = time.monotonic()
            self._observe("calls")
            self._observe("active_delta", 1.0)
            try:
                result = await invoke_with_deadline(*args, **kwargs)
            except asyncio.CancelledError:
                self._observe("cancellations")
                raise
            except Exception as error:
                if isinstance(error, grpc.aio.AioRpcError) and error.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
                    self._observe("deadlines")
                else:
                    self._observe("failures")
                raise
            else:
                self._observe("successes")
                return result
            finally:
                self._observe("call_seconds", time.monotonic() - started)
                self._observe("active_delta", -1.0)

        setattr(self, name, rpc_method_wrapper)
        return rpc_method_wrapper

    async def __aenter__(self) -> "ResilientRpcClient[StubType]":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


class PeerType(Enum):
    """ Pyrogram peer types """
    USER = 'user'
    BOT = 'bot'
    GROUP = 'group'
    CHANNEL = 'channel'
    DIRECT = 'direct'
    FORUM = 'forum'
    SUPERGROUP = 'supergroup'
    COMMUNITY = 'community'

def encode_peer_info(access_hash: int, peer_type: str, phone_number: str, last_update_on: int):
    return {"access_hash": access_hash, "peer_type": peer_type, "phone_number": phone_number, "last_update_on": last_update_on}

def decode_peer_info(peer_id: int, value):
    if value is None:
        return None
    return {
        "id": peer_id,
        "access_hash": value["access_hash"],
        "peer_type": value.get("peer_type", value.get("type")),
        "phone_number": value.get("phone_number"),
        "last_update_on": value["last_update_on"],
    }

def get_input_peer(peer):
    """ This function is almost blindly copied from pyrogram sqlite storage"""
    peer_id, peer_type, access_hash = peer['id'], peer['peer_type'], peer['access_hash']

    if peer_type in {PeerType.USER.value, PeerType.BOT.value}:
        return raw.types.InputPeerUser(user_id=peer_id, access_hash=access_hash)

    if peer_type == PeerType.GROUP.value:
        return raw.types.InputPeerChat(chat_id=-peer_id)

    if peer_type in {PeerType.DIRECT.value, PeerType.CHANNEL.value, PeerType.FORUM.value, PeerType.SUPERGROUP.value, PeerType.COMMUNITY.value}:
        return raw.types.InputPeerChannel(
            channel_id=utils.get_channel_id(peer_id),
            access_hash=access_hash
        )

    raise ValueError(f"Invalid peer type: {peer_type}")


async def fetchone(client: ResilientRpcClient[RocksDBServiceStub], column: int, keys: Any) -> Optional[Dict]:
    """ Small helper - fetches a single row from provided query """
    value_bytes: bytes | None = None
    failed = True
    retries = 0
    while failed:
        try:
            response: rockserver_storage_pb2.GetResponse = await client.get(rockserver_storage_pb2.GetRequest(transactionOrUpdateId=0, columnId=column, keys=keys))
            value_bytes = response.value
            failed = False
        except Exception as e:
            print(f"Failed to fetch an element from rocksdb ({retries} retries), retrying...", e)
            failed = True
            if retries + 1 >= 4:
                raise e
        if failed:
            await asyncio.sleep(1)
            retries += 1
    value = bson.loads(value_bytes) if value_bytes else None
    return dict(value) if value else None

class RockServerStorage(Storage):
    """
    Implementation of RockServer storage.

    Example usage:

    >>> from pyrogram import Client
    >>>
    >>> session = RockServerStorage(hostname=..., port=5332, user_id=..., session_unique_name=..., save_user_peers=...)
    >>> pyrogram = Client(session_name=session)
    >>> await pyrogram.connect()
    >>> ...

    """

    USERNAME_TTL = 8 * 60 * 60  # pyrogram constant
    MAX_SESSION_RECORD_BYTES = 64 * 1024 * 1024

    def __init__(self,
                 hostname: str,
                 port: int,
                 session_unique_name: str,
                 save_user_peers: bool,
                 rpc_observer: Optional[Callable[[str, float], None]] = None):
        """
        :param hostname: rocksdb hostname
        :param port: rocksdb port
        :param session_unique_name: telegram session phone
        """
        self._rpc_observer = rpc_observer
        self._session_col = None
        self._peer_col = None
        self._session_id = f'{session_unique_name}'
        self._session_data = {
            "dc_id": 2,
            "api_id": None,
            "server_address": None,
            "port": None,
            "test_mode": None,
            "auth_key": None,
            "date": 0,
            "user_id": None,
            "is_bot": None,
            "phone": None,
            "update_states": [],
        }
        self._initial_session_data = self._session_data.copy()
        self._channel: Channel | None = None
        self._client: ResilientRpcClient[RocksDBServiceStub] | None = None
        self._hostname = hostname
        self._port = port

        self._save_user_peers = save_user_peers

        self._username_to_id = LRU(100_000)
        self._peer_usernames = LRU(100_000)
        # Update cursors must not disappear when peer-cache capacity is reached.
        # Setters stay in RAM; the existing session-save cadence persists cursors.
        self._update_to_state = {}
        self._phone_to_id = LRU(100_000)

        self._session_lock = asyncio.Lock()

        # Older Storage initialized name; the current abstract base has no init.
        self.name = self._session_id

    async def open(self):
        """ Initialize pyrogram session"""
        channel_options = [
            # GetResponse has one bytes field: one tag and up to four length bytes
            # for a session record bounded to 64 MiB.
            ('grpc.max_receive_message_length', self.MAX_SESSION_RECORD_BYTES + 5),
            ('grpc.keepalive_time_ms', 10000),  # Send a ping every 10 seconds if no other activity
            ('grpc.keepalive_timeout_ms', 5000),  # Wait 5 seconds for the ping ack before assuming failure
            ('grpc.keepalive_permit_without_calls', True),  # Allow pings even if there are no active calls
            ('grpc.http2.min_time_between_pings_ms', 10000),  # Minimum time between pings
            ('grpc.http2.max_pings_without_data', 0),  # Allow pings even without data
            ('grpc.http2.min_ping_interval_without_data_ms', 5000),  # How often to ping if no data, useful for http2
            ('grpc.initial_reconnect_backoff_ms', 1000),  # Start with 1s backoff
            ('grpc.max_reconnect_backoff_ms', 60000),  # Max backoff of 1 minute between attempts
            ("grpc.enable_retries", True),
            ("grpc.service_config", json.dumps({
                "retryPolicy": {
                    "maxAttempts": 10,
                    "initialBackoff": "1s",
                    "maxBackoff": "10s",
                    "backoffMultiplier": 2,
                    "retryableStatusCodes": [
                        "RESOURCE_EXHAUSTED",
                        "UNAVAILABLE"
                    ]
                }
            }))
        ]
        self._client = ResilientRpcClient(hostname=self._hostname, port=self._port, compression=grpc.Compression.Gzip, stub_class=RocksDBServiceStub, channel_options=channel_options, observer=self._rpc_observer)
        try:
            await self._client.connect()
            await self.create_sessions_col()
            await self.create_data_cols()

            async with self._session_lock:
                fetched_session_data = await fetchone(self._client, self._session_col, SESSION_KEY)
                self._session_data = self._initial_session_data.copy()
                if fetched_session_data is not None:
                    self._session_data.update(fetched_session_data)
                self._update_to_state = {
                    state[0]: tuple(state) for state in self._session_data["update_states"]}
                await self._migrate_session_endpoint_no_lock()
        except BaseException:
            # Startup owns this newly allocated transport even on cancellation.
            await self.close()
            raise

    async def _migrate_session_endpoint_no_lock(self):
        if self._session_data.get("test_mode") is None:
            return

        changed = False
        test_mode = bool(self._session_data["test_mode"])
        dc_id = self._session_data.get("dc_id") or 2

        if self._session_data.get("server_address") is None:
            dc_addresses = TEST_DC_ADDRESSES if test_mode else PROD_DC_ADDRESSES
            self._session_data["server_address"] = dc_addresses[dc_id]
            changed = True

        if self._session_data.get("port") is None:
            self._session_data["port"] = 80 if test_mode else 443
            changed = True

        if changed:
            await self._save_session_data_no_lock()

    async def create_sessions_col(self):
        async with self._session_lock:
            self._session_col = cast(rockserver_storage_pb2.CreateColumnResponse, await self._client.createColumn(rockserver_storage_pb2.CreateColumnRequest(name=f'pyrogram_session_{self._session_id}', schema=rockserver_storage_pb2.ColumnSchema(fixedKeys=[1], variableTailKeys=[], hasValue=True)))).columnId

    async def create_data_cols(self):
        self._peer_col = cast(rockserver_storage_pb2.CreateColumnResponse, await self._client.createColumn(rockserver_storage_pb2.CreateColumnRequest(name=f'peers_{self._session_id}', schema=rockserver_storage_pb2.ColumnSchema(fixedKeys=[8], variableTailKeys=[], hasValue=True)))).columnId

    async def save(self):
        """ On save we update the date """
        await self.date(int(time.time()))

    async def close(self):
        """ Close transport """
        if self._client is not None:
            close_future = self._client.close()
            if close_future is not None:
                await close_future

    async def delete(self):
        """ Delete all the tables and indexes """
        await self.delete_data()
        async with self._session_lock:
            await self._client.deleteColumn(rockserver_storage_pb2.DeleteColumnRequest(columnId=self._session_col))
        self._session_data = self._initial_session_data.copy()
        self._update_to_state.clear()
        await self.create_sessions_col()

    async def delete_data(self):
        """ Delete only data, keep session """
        await self._client.deleteColumn(rockserver_storage_pb2.DeleteColumnRequest(columnId=self._peer_col))
        self._username_to_id.clear()
        self._peer_usernames.clear()
        self._phone_to_id.clear()
        await self.create_data_cols()

    # peer_id, access_hash, peer_type, phone_number
    async def update_peers(self, peers: List[Tuple[int, int, str, str]]):
        """ Copied and adopted from pyro sqlite storage"""
        if not peers:
            return

        now = int(time.time())
        deduplicated_peers = []
        seen_ids = set()

        # deduplicate peers to avoid possible `CardinalityViolation` error
        for peer in peers:
            if not self._save_user_peers and peer[2] == "user":
                continue
            peer_id, *_ = peer
            if peer_id in seen_ids:
                continue
            seen_ids.add(peer_id)
            # enrich peer with timestamp and append
            deduplicated_peers.append(tuple(chain(peer, (now,))))

        # construct insert query
        if deduplicated_peers:
            failed = True
            retries = 0
            while failed:
                try:
                    initial_request = rockserver_storage_pb2.PutMultiInitialRequest(transactionOrUpdateId=0, columnId=self._peer_col)
                    kv_list = []
                    for deduplicated_peer in deduplicated_peers:
                        peer_id = deduplicated_peer[0]
                        phone_number = deduplicated_peer[3]

                        keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
                        value_tuple = encode_peer_info(deduplicated_peer[1], deduplicated_peer[2],
                                                       phone_number, deduplicated_peer[4])
                        value = bson.dumps(value_tuple)
                        kv_list.append(rockserver_storage_pb2.KV(keys=keys, value=value))

                        if phone_number is not None:
                            self._phone_to_id[phone_number] = peer_id

                    await self._client.putMultiList(rockserver_storage_pb2.PutMultiListRequest(initialRequest=initial_request, data=kv_list))
                    failed = False
                except Exception as e:
                    print(f"Failed to update peers in rocksdb ({retries} retries), retrying...", e)
                    failed = True
                    if retries + 1 >= 4:
                        raise e
                if failed:
                    await asyncio.sleep(1)
                    retries += 1

    def _observe_username_cache(self, event: str, value: int = 1):
        if self._rpc_observer is not None:
            try:
                self._rpc_observer(event, value)
            except (Exception, asyncio.CancelledError):
                pass

    def _username_cache_error(self, username: str, reason: Optional[str] = None):
        # Reason counters are subsets of misses, not additional RPC outcomes.
        self._observe_username_cache("username_cache_misses")
        if reason is not None:
            self._observe_username_cache(reason)
        outcome = "expired" if reason == "username_cache_expired" else "not found"
        return KeyError(f"Username {outcome}: {username}")

    async def update_usernames(self, usernames: List[Tuple[int, List[str]]]):
        confirmed_at = int(time.time())
        for peer_id, id_usernames in usernames:
            current = frozenset(username.lower() for username in id_usernames if username)
            previous = self._peer_usernames.get(peer_id)
            invalidations = 0
            if previous is not None:
                for username in previous[0] - current:
                    if self._username_to_id.get(username) == peer_id:
                        self._username_to_id.pop(username, None)
                        if self._rpc_observer is not None:
                            invalidations += 1
            if current:
                self._peer_usernames[peer_id] = (current, confirmed_at)
            else:
                self._peer_usernames.pop(peer_id, None)
            for username in current:
                self._username_to_id[username] = peer_id
            self._observe_username_cache("username_cache_snapshots")
            if invalidations:
                self._observe_username_cache("username_cache_invalidations", invalidations)

    def get_cached_update_state(self, entity_id: int):
        """Read one account-local RAM entry without sorting or Rockserver I/O."""
        state = self._update_to_state.get(entity_id)
        return tuple(state) if state is not None else None

    async def get_update_states(self, ids=None):
        if ids is None:
            states = self._update_to_state.values()
        else:
            state_ids = (ids,) if isinstance(ids, int) else dict.fromkeys(ids)
            states = (self._update_to_state[state_id] for state_id in state_ids
                      if state_id in self._update_to_state)
        # SQLite orders nullable dates first, then ascending timestamps.
        return [UpdateState(*state) for state in sorted(
            states, key=lambda state: (state[3] is not None, state[3] or 0))]

    async def set_update_state(self, update_state):
        states = (update_state,) if isinstance(update_state, UpdateState) else update_state
        for state in states:
            previous = self._update_to_state.get(state.id, (state.id, None, None, None, None))
            values = (state.id, state.pts, state.qts, state.date, state.seq)
            self._update_to_state[state.id] = tuple(
                old if new is None else new for old, new in zip(previous, values))

    async def delete_update_state(self, state_id):
        state_ids = (state_id,) if isinstance(state_id, int) else state_id
        for state_id in state_ids:
            self._update_to_state.pop(state_id, None)

    async def update_state(self, value=object):
        """Legacy SDK tuple interface; new SDK callers use the methods above."""
        if value is object:
            return [(state.id, state.pts, state.qts, state.date, state.seq)
                    for state in await self.get_update_states()]
        if isinstance(value, int):
            await self.delete_update_state(value)
        else:
            # Legacy setters replace the entire tuple rather than merging fields.
            self._update_to_state[value[0]] = tuple(value)

    async def get_peer_by_id(self, peer_id: int):
        if isinstance(peer_id, str) or (not self._save_user_peers and peer_id > 0):
            raise KeyError(f"ID not found: {peer_id}")

        keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
        encoded_value = await fetchone(self._client, self._peer_col, keys)
        value_tuple = decode_peer_info(peer_id, encoded_value)
        if value_tuple is None:
            raise KeyError(f"ID not found: {peer_id}")

        return get_input_peer(value_tuple)

    async def get_peer_by_username(self, username: str):
        username = username.lower()
        peer_id = self._username_to_id.get(username)
        current = self._peer_usernames.get(peer_id) if peer_id is not None else None
        if current is None or username not in current[0]:
            raise self._username_cache_error(username)
        if int(time.time() - current[1]) > self.USERNAME_TTL:
            raise self._username_cache_error(username, "username_cache_expired")

        keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
        encoded_value = await fetchone(self._client, self._peer_col, keys)
        current = self._peer_usernames.get(peer_id)
        if (self._username_to_id.get(username) != peer_id
                or current is None or username not in current[0]):
            raise self._username_cache_error(username, "username_cache_changed_during_read")
        now = time.time()
        if int(now - current[1]) > self.USERNAME_TTL:
            raise self._username_cache_error(username, "username_cache_expired")
        value_tuple = decode_peer_info(peer_id, encoded_value)

        if value_tuple is None:
            raise self._username_cache_error(username)

        if int(now - value_tuple['last_update_on']) > self.USERNAME_TTL:
            raise self._username_cache_error(username, "username_cache_expired")

        peer = get_input_peer(value_tuple)
        self._observe_username_cache("username_cache_hits")
        return peer

    async def get_peer_by_phone_number(self, phone_number: str):
        peer_id = self._phone_to_id.get(phone_number)

        if peer_id is None:
            raise KeyError(f"Phone number not found: {phone_number}")

        keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
        encoded_value = await fetchone(self._client, self._peer_col, keys)
        value_tuple = decode_peer_info(peer_id, encoded_value)

        return get_input_peer(value_tuple)

    async def _set(self, column, value: Any):
        async with self._session_lock:
            await self._set_no_lock(column, value)

    async def _set_no_lock(self, column, value: Any):
        self._session_data[column] = value  # update local copy
        await self._save_session_data_no_lock()

    async def _save_session_data_no_lock(self):
        # Freeze one snapshot before the first RPC: retries must write the same
        # bytes even if update cursors change while the transport awaits a reply.
        self._session_data["update_states"] = list(self._update_to_state.values())
        encoded_session_data: bytes = bson.dumps(self._session_data)
        if len(encoded_session_data) > self.MAX_SESSION_RECORD_BYTES:
            raise ValueError(
                f"Session record exceeds {self.MAX_SESSION_RECORD_BYTES} bytes; "
                "the previous saved session was not overwritten")
        failed = True
        retries = 0
        while failed:
            try:
                await self._client.put(rockserver_storage_pb2.PutRequest(transactionOrUpdateId=0, columnId=self._session_col, data=rockserver_storage_pb2.KV(keys=SESSION_KEY, value=encoded_session_data)))
                failed = False
            except Exception as e:
                print(f"Failed to update session in rocksdb ({retries} retries), cancelling the update transaction and retrying...", e)
                failed = True
                if retries + 1 >= 4:
                    raise e
            if failed:
                await asyncio.sleep(1)
                retries += 1

    async def _accessor(self, column, value: Any = object):
        async with self._session_lock:
            return self._session_data[column] if value == object else await self._set_no_lock(column, value)

    async def dc_id(self, value: int = object):
        return await self._accessor('dc_id', value)

    async def api_id(self, value: int = object):
        return await self._accessor('api_id', value)

    async def server_address(self, value: str = object):
        return await self._accessor('server_address', value)

    async def port(self, value: int = object):
        return await self._accessor('port', value)

    async def test_mode(self, value: bool = object):
        return await self._accessor('test_mode', value)

    async def auth_key(self, value: bytes = object):
        return await self._accessor('auth_key', value)

    async def date(self, value: int = object):
        return await self._accessor('date', value)

    async def user_id(self, value: int = object):
        return await self._accessor('user_id', value)

    async def is_bot(self, value: bool = object):
        return await self._accessor('is_bot', value)
