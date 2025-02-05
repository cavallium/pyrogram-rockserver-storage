__author__ = 'Andrea Cavalli'
__version__ = '0.2'

import asyncio
import pathlib
import time
import urllib
import warnings
from enum import Enum
from itertools import chain
from string import digits
from typing import Any, List, Tuple, Dict, Optional

from pyrogram import raw, utils
from pyrogram.storage import Storage
from thriftpy2.contrib.aio.socket import TAsyncSocket
from thriftpy2.contrib.aio.transport import TAsyncFramedTransportFactory, TAsyncBufferedTransportFactory
from thriftpy2.contrib.aio.protocol import TAsyncBinaryProtocolFactory

from lru import LRU

import thriftpy2

import bson
from thriftpy2.contrib.aio.client import TAsyncClient

from pyrogram_rockserver_storage.TParallelAsyncClient import TParallelAsyncClient

SESSION_KEY = [bytes([0])]
DIGITS = set(digits)
TRANSPORT_FACTORY = TAsyncFramedTransportFactory()
PROTOCOL_FACTORY = TAsyncBinaryProtocolFactory()

rocksdb_thrift = thriftpy2.load(str((pathlib.Path(__file__).parent / pathlib.Path("rocksdb.thrift")).resolve(strict=False)), module_name="rocksdb_thrift")

class PeerType(Enum):
    """ Pyrogram peer types """
    USER = 'user'
    BOT = 'bot'
    GROUP = 'group'
    CHANNEL = 'channel'
    SUPERGROUP = 'supergroup'

def encode_peer_info(access_hash: int, peer_type: str, phone_number: str, last_update_on: int):
    return {"access_hash": access_hash, "peer_type": peer_type, "phone_number": phone_number, "last_update_on": last_update_on}

def decode_peer_info(peer_id: int, value):
    return {"id": peer_id, "access_hash": value["access_hash"], "peer_type": value["peer_type"], "phone_number": ["phone_number"], "last_update_on": value["last_update_on"]} if value is not None else None

def get_input_peer(peer):
    """ This function is almost blindly copied from pyrogram sqlite storage"""
    peer_id, peer_type, access_hash = peer['id'], peer['peer_type'], peer['access_hash']

    if peer_type in {PeerType.USER.value, PeerType.BOT.value}:
        return raw.types.InputPeerUser(user_id=peer_id, access_hash=access_hash)

    if peer_type == PeerType.GROUP.value:
        return raw.types.InputPeerChat(chat_id=-peer_id)

    if peer_type in {PeerType.CHANNEL.value, PeerType.SUPERGROUP.value}:
        return raw.types.InputPeerChannel(
            channel_id=utils.get_channel_id(peer_id),
            access_hash=access_hash
        )

    raise ValueError(f"Invalid peer type: {peer['type']}")


async def fetchone(client: TAsyncClient, column: int, keys: Any) -> Optional[Dict]:
    """ Small helper - fetches a single row from provided query """
    value = (await client.get(0, column, keys)).value
    value = bson.loads(value) if value else None
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

    def __init__(self,
                 hostname: str,
                 port: int,
                 session_unique_name: str,
                 save_user_peers: bool):
        """
        :param hostname: rocksdb hostname
        :param port: rocksdb port
        :param session_unique_name: telegram session phone
        """
        self._session_col = None
        self._peer_col = None
        self._session_id = f'{session_unique_name}'
        self._session_data = {"dc_id": 2, "api_id": None, "test_mode": None, "auth_key": None, "date": 0, "user_id": None, "is_bot": None, "phone": None}
        self._client = None
        self._hostname = hostname
        self._port = port

        self._save_user_peers = save_user_peers

        self._username_to_id = LRU(100_000)
        self._update_to_state = LRU(100_000)
        self._phone_to_id = LRU(100_000)

        super().__init__(name=self._session_id)

    async def make_parallel_aio_client(self, service, host='localhost', port=9090, unix_socket=None,
                          proto_factory=TAsyncBinaryProtocolFactory(),
                          trans_factory=TAsyncBufferedTransportFactory(),
                          timeout=3000, connect_timeout=None,
                          cafile=None, ssl_context=None,
                          certfile=None, keyfile=None,
                          validate=True, url='',
                          socket_timeout=None):
        if socket_timeout is not None:
            warnings.warn(
                "The 'socket_timeout' argument is deprecated. "
                "Please use 'timeout' instead.",
                DeprecationWarning,
            )
            timeout = socket_timeout
        if url:
            parsed_url = urllib.parse.urlparse(url)
            host = parsed_url.hostname or host
            port = parsed_url.port or port
        if unix_socket:
            socket = TAsyncSocket(unix_socket=unix_socket,
                                  connect_timeout=connect_timeout,
                                  socket_timeout=timeout)
            if certfile:
                warnings.warn("SSL only works with host:port, not unix_socket.")
        elif host and port:
            socket = TAsyncSocket(
                host, port,
                socket_timeout=timeout, connect_timeout=connect_timeout,
                cafile=cafile, ssl_context=ssl_context,
                certfile=certfile, keyfile=keyfile, validate=validate)
        else:
            raise ValueError("Either host/port or unix_socket"
                             " or url must be provided.")

        transport = trans_factory.get_transport(socket)
        protocol = proto_factory.get_protocol(transport)
        await transport.open()
        return TParallelAsyncClient(service, protocol)

    async def open(self):
        """ Initialize pyrogram session"""
        self._client = await self.make_parallel_aio_client(rocksdb_thrift.RocksDB, host=self._hostname, port=self._port, trans_factory=TRANSPORT_FACTORY, proto_factory=PROTOCOL_FACTORY, connect_timeout=8000)

        # Column('dc_id', BIGINT, primary_key=True),
        # Column('api_id', BIGINT),
        # Column('test_mode', Boolean),
        # Column('auth_key', BYTEA),
        # Column('date', BIGINT, nullable=False),
        # Column('user_id', BIGINT),
        # Column('is_bot', Boolean),
        # Column('phone', String(length=50)
        self._session_col = await self._client.createColumn(name=f'pyrogram_session_{self._session_id}', schema=rocksdb_thrift.ColumnSchema(fixedKeys=[1], variableTailKeys=[], hasValue=True))

        # Column('id', BIGINT),
        # Column('access_hash', BIGINT),
        # Column('type', String, nullable=False),
        # Column('username', String),
        # Column('phone_number', String),
        # Column('last_update_on', BIGINT),
        self._peer_col = await self._client.createColumn(name=f'peers_{self._session_id}', schema=rocksdb_thrift.ColumnSchema(fixedKeys=[8], variableTailKeys=[], hasValue=True))

        fetched_session_data = await fetchone(self._client, self._session_col, SESSION_KEY)
        self._session_data = fetched_session_data if fetched_session_data is not None else self._session_data

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
        await self._client.deleteColumn(self._session_col)

    async def delete_data(self):
        """ Delete only data, keep session """
        await self._client.deleteColumn(self._peer_col)

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
            keys_multi = []
            value_multi = []
            for deduplicated_peer in deduplicated_peers:
                peer_id = deduplicated_peer[0]
                phone_number = deduplicated_peer[3]

                keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
                value_tuple = encode_peer_info(deduplicated_peer[1], deduplicated_peer[2],
                                               phone_number, deduplicated_peer[4])
                value = bson.dumps(value_tuple)
                keys_multi.append(keys)
                value_multi.append(value)

                if phone_number is not None:
                    self._phone_to_id[phone_number] = peer_id

            failed = True
            while failed:
                try:
                    await self._client.putMulti(0, self._peer_col, keys_multi, value_multi)
                    failed = False
                except Exception as e:
                    print("Failed to update peers in rocksdb, retrying...", e)
                    failed = True
                if failed:
                    await asyncio.sleep(1)

    async def update_usernames(self, usernames: List[Tuple[int, List[str]]]):
        for t in usernames:
            peer_id = t[0]
            id_usernames = t[1]
            for username in id_usernames:
                self._username_to_id[username] = peer_id

    async def update_state(self, value: Tuple[int, int, int, int, int] = object):
        if value == object:
            return sorted(self._update_to_state.values(), key=lambda x: x[3], reverse=False)
        else:
            if isinstance(value, int):
                self._update_to_state.pop(value)
            else:
                self._update_to_state[value[0]] = value

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
        peer_id = self._username_to_id.get(username)

        if peer_id is None:
            raise KeyError(f"Username not found: {username}")

        keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
        encoded_value = await fetchone(self._client, self._peer_col, keys)
        value_tuple = decode_peer_info(peer_id, encoded_value)

        if value_tuple is None:
            raise KeyError(f"Username not found: {username}")

        if int(time.time() - value_tuple['last_update_on']) > self.USERNAME_TTL:
            raise KeyError(f"Username expired: {username}")

        return get_input_peer(value_tuple)

    async def get_peer_by_phone_number(self, phone_number: str):
        peer_id = self._phone_to_id.get(phone_number)

        if peer_id is None:
            raise KeyError(f"Phone number not found: {phone_number}")

        keys = [peer_id.to_bytes(8, byteorder='big', signed=True)]
        encoded_value = await fetchone(self._client, self._peer_col, keys)
        value_tuple = decode_peer_info(peer_id, encoded_value)

        return get_input_peer(value_tuple)

    async def _set(self, column, value: Any):
        self._session_data[column] = value  # update local copy
        failed = True
        while failed:
            update_begin = await self._client.getForUpdate(0, self._session_col, SESSION_KEY)
            try:
                decoded_bson_session_data = bson.loads(
                    update_begin.previous) if update_begin.previous is not None else None
                if decoded_bson_session_data is not None:
                    session_data = decoded_bson_session_data
                    session_data[column] = value
                else:
                    session_data = self._session_data
                encoded_session_data = bson.dumps(session_data)
                await self._client.put(update_begin.updateId, self._session_col, SESSION_KEY, encoded_session_data)
                failed = False
            except Exception as e:
                print("Failed to update session in rocksdb, cancelling the update transaction and retrying...", e)
                failed = True
                try:
                    await self._client.closeFailedUpdate(update_begin.updateId)
                except:
                    pass
            if failed:
                await asyncio.sleep(1)

    async def _accessor(self, column, value: Any = object):
        return self._session_data[column] if value == object else await self._set(column, value)

    async def dc_id(self, value: int = object):
        return await self._accessor('dc_id', value)

    async def api_id(self, value: int = object):
        return await self._accessor('api_id', value)

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
