__author__ = 'Andrea Cavalli'
__version__ = '0.2'

import asyncio
import json
import time
import urllib
import warnings
from enum import Enum
from itertools import chain
from string import digits
from typing import Any, List, Tuple, Dict, Optional, cast

import grpc.aio
from grpc import Channel
from pyrogram import raw, utils
from pyrogram.storage import Storage

from lru import LRU

import bson

import pyrogram_rockserver_storage.rocksdb_pb2 as rockserver_storage_pb2
from pyrogram_rockserver_storage.rocksdb_pb2_grpc import RocksDBServiceStub

SESSION_KEY = [bytes([0])]
DIGITS = set(digits)

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


async def fetchone(client: rocksdb_pb2_grpc.RocksDBServiceStub, column: int, keys: Any) -> Optional[Dict]:
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
        self._channel: Channel | None = None
        self._client: RocksDBServiceStub | None = None
        self._hostname = hostname
        self._port = port

        self._save_user_peers = save_user_peers

        self._username_to_id = LRU(100_000)
        self._update_to_state = LRU(100_000)
        self._phone_to_id = LRU(100_000)

        super().__init__(name=self._session_id)

    async def open(self):
        """ Initialize pyrogram session"""
        channel_options = [
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
        self._channel = grpc.aio.insecure_channel(target=f'{self._hostname}:{self._port}', compression=grpc.Compression.Gzip, options=channel_options)
        self._client = RocksDBServiceStub(self._channel)

        # Column('dc_id', BIGINT, primary_key=True),
        # Column('api_id', BIGINT),
        # Column('test_mode', Boolean),
        # Column('auth_key', BYTEA),
        # Column('date', BIGINT, nullable=False),
        # Column('user_id', BIGINT),
        # Column('is_bot', Boolean),
        # Column('phone', String(length=50)
        await self.create_sessions_col()

        # Column('id', BIGINT),
        # Column('access_hash', BIGINT),
        # Column('type', String, nullable=False),
        # Column('username', String),
        # Column('phone_number', String),
        # Column('last_update_on', BIGINT),
        await self.create_data_cols()

        fetched_session_data = await fetchone(self._client, self._session_col, SESSION_KEY)
        self._session_data = fetched_session_data if fetched_session_data is not None else self._session_data

    async def create_sessions_col(self):
        self._session_col = cast(rockserver_storage_pb2.CreateColumnResponse, await self._client.createColumn(rockserver_storage_pb2.CreateColumnRequest(name=f'pyrogram_session_{self._session_id}', schema=rockserver_storage_pb2.ColumnSchema(fixedKeys=[1], variableTailKeys=[], hasValue=True)))).columnId

    async def create_data_cols(self):
        self._peer_col = cast(rockserver_storage_pb2.CreateColumnResponse, await self._client.createColumn(rockserver_storage_pb2.CreateColumnRequest(name=f'peers_{self._session_id}', schema=rockserver_storage_pb2.ColumnSchema(fixedKeys=[8], variableTailKeys=[], hasValue=True)))).columnId

    async def save(self):
        """ On save we update the date """
        await self.date(int(time.time()))

    async def close(self):
        """ Close transport """
        if self._client is not None:
            close_future = self._channel.close()
            if close_future is not None:
                await close_future

    async def delete(self):
        """ Delete all the tables and indexes """
        await self.delete_data()
        await self._client.deleteColumn(rockserver_storage_pb2.DeleteColumnRequest(columnId=self._session_col))
        await self.create_sessions_col()

    async def delete_data(self):
        """ Delete only data, keep session """
        await self._client.deleteColumn(rockserver_storage_pb2.DeleteColumnRequest(columnId=self._peer_col))
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
                        if value:
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
        retries = 0
        while failed:
            update_begin = cast(rockserver_storage_pb2.UpdateBegin, await self._client.getForUpdate(rockserver_storage_pb2.GetRequest(transactionOrUpdateId=0, columnId=self._session_col, keys=SESSION_KEY)))
            try:
                decoded_bson_session_data = bson.loads(
                    update_begin.previous) if update_begin.previous else None
                if decoded_bson_session_data is not None:
                    session_data = decoded_bson_session_data
                    session_data[column] = value
                else:
                    session_data = self._session_data
                encoded_session_data: bytes = bson.dumps(session_data)
                await self._client.put(rockserver_storage_pb2.PutRequest(transactionOrUpdateId=update_begin.updateId, columnId=self._session_col, data=rockserver_storage_pb2.KV(keys=SESSION_KEY, value=encoded_session_data)))
                failed = False
            except Exception as e:
                print(f"Failed to update session in rocksdb ({retries} retries), cancelling the update transaction and retrying...", e)
                failed = True
                try:
                    await self._client.closeFailedUpdate(rockserver_storage_pb2.CloseFailedUpdateRequest(updateId=update_begin.updateId))
                except:
                    pass
                if retries + 1 >= 4:
                    raise e
            if failed:
                await asyncio.sleep(1)
                retries += 1

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
