import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bson
import grpc

import pyrogram_rockserver_storage as storage
from pyrogram_rockserver_storage import rocksdb_pb2 as pb


class RecordingStub:
    def __init__(self, channel):
        self.calls = []
        self.fail_next = False

    def __getattr__(self, name):
        async def call(request, **kwargs):
            copied = type(request)()
            copied.CopyFrom(request)
            self.calls.append((name, copied, kwargs))
            if self.fail_next:
                self.fail_next = False
                raise grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), ())
            if name == 'createColumn':
                return pb.CreateColumnResponse(columnId=len(self.calls))
            if name == 'get':
                return pb.GetResponse()
        return call


class RequestContextTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        client = storage.ResilientRpcClient('unused', 0, RecordingStub)
        client._stub = RecordingStub(None)
        return client

    async def test_actual_session_operations_send_legal_contexts_and_preserve_data(self):
        client = self.client()
        session = storage.RockServerStorage('unused', 0, 'context-test', True)
        session._client = client
        await session.create_sessions_col()
        await session.create_data_cols()
        await storage.fetchone(client, session._session_col, storage.SESSION_KEY)
        await session.dc_id(2)
        await session.update_peers([(42, 91, 'user', '123')])
        await session.delete()
        self.assertEqual([name for name, _, _ in client._stub.calls],
                         ['createColumn', 'createColumn', 'get', 'put', 'putMultiList',
                          'deleteColumn', 'createColumn', 'deleteColumn', 'createColumn'])
        profiles = {'createColumn': pb.BATCH, 'deleteColumn': pb.BATCH,
                    'get': pb.LATENCY, 'put': pb.INGEST, 'putMultiList': pb.INGEST}
        for name, request, options in client._stub.calls:
            context = request.initialRequest.context if name == 'putMultiList' else request.context
            self.assertEqual(context.profile, profiles[name])
            self.assertEqual(context.workloadContractVersion, 3)
            self.assertGreater(context.timeoutNanos, 0)
            self.assertLessEqual(context.timeoutNanos, int(options['timeout'] * 1_000_000_000))
            self.assertLessEqual(options['timeout'], 30)
            self.assertTrue(options['wait_for_ready'])
        session_create = client._stub.calls[0][1]
        peer_create = client._stub.calls[1][1]
        self.assertEqual(list(session_create.schema.fixedKeys), [1])
        self.assertEqual(list(peer_create.schema.fixedKeys), [8])
        session_put = client._stub.calls[3][1]
        self.assertEqual(list(session_put.data.keys), storage.SESSION_KEY)
        self.assertEqual(bson.loads(session_put.data.value), session._session_data)
        peer_put = client._stub.calls[4][1]
        self.assertEqual(list(peer_put.data[0].keys), [b'\x00\x00\x00\x00\x00\x00\x00*'])
        self.assertEqual(bson.loads(peer_put.data[0].value)['access_hash'], 91)

    async def test_retry_context_and_transport_use_remaining_connection_and_backoff_budget(self):
        for name, request in (
                ('put', pb.PutRequest(columnId=7, data=pb.KV(keys=[b'key'], value=b'value'))),
                ('putMultiList', pb.PutMultiListRequest(initialRequest=pb.PutMultiInitialRequest(columnId=7),
                                                       data=[pb.KV(keys=[b'key'], value=b'value')]))):
            with self.subTest(name=name):
                client = self.client()
                client._initial_backoff_ms = 200
                client._stub.fail_next = True
                clock = [100.0]

                async def connect():
                    clock[0] += 0.125

                async def sleep(seconds):
                    clock[0] += seconds

                client.connect = connect
                before = request.SerializeToString()
                with patch.object(storage, 'time', SimpleNamespace(monotonic=lambda: clock[0])), \
                        patch.object(storage.random, 'randint', return_value=0), \
                        patch.object(storage.asyncio, 'sleep', new=sleep):
                    await getattr(client, name)(request=request, timeout=1)
                self.assertEqual(request.SerializeToString(), before)
                self.assertEqual(len(client._stub.calls), 2)
                for (_, copied, options), expected in zip(client._stub.calls, (0.875, 0.675)):
                    context = copied.initialRequest.context if name == 'putMultiList' else copied.context
                    self.assertAlmostEqual(options['timeout'], expected)
                    self.assertEqual(context.timeoutNanos, int(options['timeout'] * 1_000_000_000))
                    self.assertEqual(context.profile, pb.INGEST)
                    self.assertEqual(context.workloadContractVersion, 3)

    async def test_concurrent_reuse_keeps_each_request_and_context_private(self):
        import asyncio
        client = self.client()
        request = pb.GetRequest(columnId=7, keys=[b'key'])
        await asyncio.gather(client.get(request, timeout=1), client.get(request, timeout=2))
        self.assertFalse(request.HasField('context'))
        calls = client._stub.calls
        self.assertIsNot(calls[0][1], calls[1][1])
        self.assertLess(calls[0][1].context.timeoutNanos, 1_000_000_000)
        self.assertGreater(calls[1][1].context.timeoutNanos, 1_000_000_000)
