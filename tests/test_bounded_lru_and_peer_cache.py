import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import bson
import grpc
from google.protobuf.empty_pb2 import Empty

import pyrogram_rockserver_storage as storage
from pyrogram_rockserver_storage import BoundedLRU, RockServerStorage
from pyrogram_rockserver_storage import rocksdb_pb2 as pb
from pyrogram_rockserver_storage import rocksdb_pb2_grpc as service


class BoundedLRUTests(unittest.TestCase):
    def test_validation_rejects_non_positive_or_bool_maxsize(self):
        for bad in (0, -1, True, False, 1.5, "10"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    BoundedLRU(bad)

    def test_recency_refresh_eviction_copy_and_resize(self):
        cache = BoundedLRU(3)
        self.assertEqual(3, cache.get_size())
        cache["a"] = 1
        cache["b"] = 2
        cache["c"] = 3

        # __getitem__ promotes "a" to MRU.
        self.assertEqual(1, cache["a"])
        # get() promotes "b" to MRU; "c" is now LRU.
        self.assertEqual(2, cache.get("b"))
        self.assertIsNone(cache.get("missing"))
        self.assertEqual("fallback", cache.get("missing", "fallback"))

        cache["d"] = 4
        self.assertNotIn("c", cache)
        self.assertEqual(["a", "b", "d"], list(cache.keys()))

        # copy() keeps maxsize and LRU-to-MRU order.
        copied = cache.copy()
        self.assertIsInstance(copied, BoundedLRU)
        self.assertEqual(3, copied.maxsize)
        self.assertEqual(list(cache.items()), list(copied.items()))

        # setdefault / update enforce maxsize and recency.
        self.assertEqual(1, cache.setdefault("a", 99))
        self.assertEqual(5, cache.setdefault("e", 5))
        self.assertNotIn("b", cache)
        cache.update([("f", 6), ("g", 7)])
        self.assertEqual(["e", "f", "g"], list(cache.keys()))

        # set_size shrinks and evicts oldest entries.
        cache.set_size(2)
        self.assertEqual(2, cache.get_size())
        self.assertEqual(["f", "g"], list(cache.keys()))
        with self.assertRaises(ValueError):
            cache.set_size(0)


class CompressionAndPeerCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_storage_compression_is_configurable_and_supports_no_compression(self):
        class Backend(service.RocksDBServiceServicer):
            def __init__(self):
                self.columns = {}
                self.rows = {}

            def createColumn(self, request, context):
                col = self.columns.setdefault(request.name, len(self.columns) + 1)
                return pb.CreateColumnResponse(columnId=col)

            def get(self, request, context):
                key = request.keys[0] if request.keys else b""
                return pb.GetResponse(value=self.rows.get((request.columnId, key), b""))

            def putMultiList(self, request, context):
                col = request.initialRequest.columnId
                for kv in request.data:
                    self.rows[(col, kv.keys[0])] = kv.value
                return Empty()

        executor = ThreadPoolExecutor(max_workers=2)
        server = grpc.server(executor)
        service.add_RocksDBServiceServicer_to_server(Backend(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        try:
            default_storage = RockServerStorage("127.0.0.1", port, "comp-default", False)
            lan_storage = RockServerStorage(
                "127.0.0.1",
                port,
                "comp-lan",
                False,
                compression=grpc.Compression.NoCompression,
            )
            none_storage = RockServerStorage(
                "127.0.0.1",
                port,
                "comp-none",
                False,
                compression=None,
            )
            try:
                await default_storage.open()
                await lan_storage.open()
                await none_storage.open()
                self.assertEqual(grpc.Compression.Gzip, default_storage._client._compression)
                self.assertEqual(grpc.Compression.NoCompression, lan_storage._client._compression)
                self.assertIsNone(none_storage._client._compression)

                await lan_storage.update_peers([(-1000000000123, 777, "channel", None)])
                peer = await lan_storage.get_peer_by_id(-1000000000123)
                self.assertEqual(123, peer.channel_id)
                self.assertEqual(777, peer.access_hash)
            finally:
                await default_storage.close()
                await lan_storage.close()
                await none_storage.close()
        finally:
            server.stop(0).wait()
            executor.shutdown(wait=True)

    async def test_peer_cache_hit_eviction_update_and_delete_invalidation(self):
        class Backend(service.RocksDBServiceServicer):
            def __init__(self):
                self.columns = {}
                self.rows = {}
                self.get_calls = 0

            def createColumn(self, request, context):
                col = self.columns.setdefault(request.name, len(self.columns) + 1)
                return pb.CreateColumnResponse(columnId=col)

            def deleteColumn(self, request, context):
                for key in [k for k in self.rows if k[0] == request.columnId]:
                    del self.rows[key]
                return Empty()

            def get(self, request, context):
                self.get_calls += 1
                key = request.keys[0] if request.keys else b""
                return pb.GetResponse(value=self.rows.get((request.columnId, key), b""))

            def put(self, request, context):
                self.rows[(request.columnId, request.data.keys[0])] = request.data.value
                return Empty()

            def putMultiList(self, request, context):
                col = request.initialRequest.columnId
                for kv in request.data:
                    self.rows[(col, kv.keys[0])] = kv.value
                return Empty()

        backend = Backend()
        executor = ThreadPoolExecutor(max_workers=2)
        server = grpc.server(executor)
        service.add_RocksDBServiceServicer_to_server(backend, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()

        client = RockServerStorage(
            "127.0.0.1",
            port,
            "peer-cache-test",
            save_user_peers=False,
            compression=grpc.Compression.NoCompression,
            peer_cache_size=2,
        )
        try:
            for attr in ("_username_to_id", "_peer_usernames", "_phone_to_id", "_peer_cache"):
                self.assertIsInstance(getattr(client, attr), BoundedLRU)
            self.assertEqual(2, client._peer_cache.maxsize)

            await client.open()
            gets_after_open = backend.get_calls

            peer_a = -1000000000101
            peer_b = -1000000000102
            peer_c = -1000000000103

            # Seed peer_a directly in backend to test read-through miss -> hit.
            key_a = peer_a.to_bytes(8, byteorder="big", signed=True)
            backend.rows[(client._peer_col, key_a)] = bson.dumps(
                storage.encode_peer_info(111, "channel", None, 1)
            )

            got_a1 = await client.get_peer_by_id(peer_a)
            self.assertEqual(111, got_a1.access_hash)
            self.assertEqual(gets_after_open + 1, backend.get_calls)

            # Second read hits _peer_cache without RPC.
            got_a2 = await client.get_peer_by_id(peer_a)
            self.assertEqual(111, got_a2.access_hash)
            self.assertEqual(gets_after_open + 1, backend.get_calls)

            # update_peers refreshes cache immediately and invalidates stale values.
            await client.update_peers(
                [
                    (peer_a, 222, "channel", None),
                    (peer_a, 999, "channel", None),  # duplicate id ignored
                    (42, 333, "user", None),  # skipped when save_user_peers=False
                ]
            )
            got_a3 = await client.get_peer_by_id(peer_a)
            self.assertEqual(222, got_a3.access_hash)
            self.assertEqual(gets_after_open + 1, backend.get_calls)

            with self.assertRaises(KeyError):
                await client.get_peer_by_id(42)
            self.assertEqual(gets_after_open + 1, backend.get_calls)

            # BoundedLRU(2) evicts LRU entry when 2 more entries are inserted.
            await client.update_peers([(peer_b, 444, "channel", None)])
            # Touch peer_a so peer_b becomes LRU.
            await client.get_peer_by_id(peer_a)
            await client.update_peers([(peer_c, 555, "channel", None)])
            self.assertIn(peer_a, client._peer_cache)
            self.assertIn(peer_c, client._peer_cache)
            self.assertNotIn(peer_b, client._peer_cache)

            # Evicted peer_b falls back to backend RPC and refreshes cache.
            got_b = await client.get_peer_by_id(peer_b)
            self.assertEqual(444, got_b.access_hash)
            self.assertEqual(gets_after_open + 2, backend.get_calls)

            # Invalid peer_type in update_peers still invalidates stale cached entry.
            await client.update_peers([(peer_b, 666, "invalid", None)])
            self.assertNotIn(peer_b, client._peer_cache)

            # delete_data() clears _peer_cache.
            await client.update_peers([(peer_a, 777, "channel", None)])
            self.assertIn(peer_a, client._peer_cache)
            await client.delete_data()
            self.assertEqual(0, len(client._peer_cache))
            with self.assertRaises(KeyError):
                await client.get_peer_by_id(peer_a)

            # delete() also clears _peer_cache.
            await client.update_peers([(peer_a, 888, "channel", None)])
            self.assertIn(peer_a, client._peer_cache)
            await client.delete()
            self.assertEqual(0, len(client._peer_cache))
        finally:
            await client.close()
            server.stop(0).wait()
            executor.shutdown(wait=True)

    async def test_concurrent_update_peers_does_not_get_overwritten_by_stale_get(self):
        store = RockServerStorage("unused", 0, "race-test", False)
        store._peer_col = 1
        peer_id = -1000000000201
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_fetch(*args):
            entered.set()
            await release.wait()
            return storage.encode_peer_info(111, "channel", None, 1)

        class StubClient:
            async def putMultiList(self, request):
                return Empty()

        store._client = StubClient()
        with patch.object(storage, "fetchone", side_effect=slow_fetch):
            task = asyncio.create_task(store.get_peer_by_id(peer_id))
            await entered.wait()
            await store.update_peers([(peer_id, 999, "channel", None)])
            release.set()
            stale_result = await task

        # In-flight read completed without crashing, and cached value stays 999 (not 111).
        self.assertEqual(111, stale_result.access_hash)
        cached = await store.get_peer_by_id(peer_id)
        self.assertEqual(999, cached.access_hash)

    async def test_concurrent_delete_data_prevents_stale_get_and_update_from_repopulating_cache(self):
        store = RockServerStorage("unused", 0, "delete-race-test", False)
        store._peer_col = 1
        peer_a = -1000000000301
        peer_b = -1000000000302
        get_entered = asyncio.Event()
        get_release = asyncio.Event()
        put_entered = asyncio.Event()
        put_release = asyncio.Event()

        async def slow_fetch(*args):
            get_entered.set()
            await get_release.wait()
            return storage.encode_peer_info(111, "channel", None, 1)

        class RacingClient:
            async def putMultiList(self, request):
                put_entered.set()
                await put_release.wait()
                return Empty()

            async def deleteColumn(self, request):
                return Empty()

            async def createColumn(self, request):
                return pb.CreateColumnResponse(columnId=2)

        store._client = RacingClient()
        with patch.object(storage, "fetchone", side_effect=slow_fetch):
            get_task = asyncio.create_task(store.get_peer_by_id(peer_a))
            update_task = asyncio.create_task(store.update_peers([(peer_b, 222, "channel", None)]))
            await get_entered.wait()
            await put_entered.wait()
            await store.delete_data()
            get_release.set()
            put_release.set()
            await get_task
            await update_task

        self.assertEqual(0, len(store._peer_cache))

    def test_imports_cleanly_without_lru_dict(self):
        import os
        import pathlib
        import subprocess
        import sys

        repo_root = str(pathlib.Path(__file__).resolve().parents[1])
        env = dict(os.environ)
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{existing_pp}" if existing_pp else repo_root
        script = (
            "import sys; sys.modules['lru'] = None; "
            "import pyrogram_rockserver_storage as s; "
            "assert s.LRU is s.BoundedLRU; "
            "c = s.BoundedLRU(2); c['x'] = 1; assert c['x'] == 1"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr or proc.stdout)
