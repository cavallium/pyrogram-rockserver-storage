import unittest

import grpc
from google.protobuf.empty_pb2 import Empty

from pyrogram_rockserver_storage import RockServerStorage, UpdateState
from pyrogram_rockserver_storage import rocksdb_pb2 as pb
from pyrogram_rockserver_storage.rocksdb_pb2_grpc import (
    RocksDBServiceServicer,
    add_RocksDBServiceServicer_to_server,
)


class SessionService(RocksDBServiceServicer):
    def __init__(self):
        self.columns = {}
        self.rows = {}
        self.put_count = 0

    async def createColumn(self, request, context):
        column = self.columns.setdefault(request.name, len(self.columns) + 1)
        return pb.CreateColumnResponse(columnId=column)

    async def put(self, request, context):
        self.put_count += 1
        self.rows[request.columnId] = request.data.value
        return Empty()

    async def get(self, request, context):
        return pb.GetResponse(value=self.rows.get(request.columnId, b''))


class SessionRecordGrpcTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_larger_than_default_receive_limit_reopens_over_grpc(self):
        # Match the real RockServer inbound limit without changing its source.
        server = grpc.aio.server(options=[('grpc.max_receive_message_length', 512 * 1024 * 1024)])
        service = SessionService()
        add_RocksDBServiceServicer_to_server(service, server)
        port = server.add_insecure_port('127.0.0.1:0')
        await server.start()
        self.addAsyncCleanup(server.stop, 0)
        session = RockServerStorage('127.0.0.1', port, 'large-snapshot', True)
        self.addAsyncCleanup(session.close)
        await session.open()
        await session.auth_key(b'a' * 256)
        count = 100_000
        await session.set_update_state(UpdateState(i, i, i, i, i) for i in range(count))
        await session.save()
        snapshot = service.rows[session._session_col]
        self.assertGreater(len(snapshot), 4 * 1024 * 1024)
        self.assertLessEqual(len(snapshot), session.MAX_SESSION_RECORD_BYTES)
        # One auth-key save and one cursor save; setting 100k cursors adds no RPC.
        self.assertEqual(2, service.put_count)
        await session.close()
        reopened = RockServerStorage('127.0.0.1', port, 'large-snapshot', True)
        self.addAsyncCleanup(reopened.close)
        await reopened.open()
        self.assertEqual(b'a' * 256, await reopened.auth_key())
        states = await reopened.get_update_states()
        self.assertEqual(count, len(states))
        self.assertEqual(UpdateState(0, 0, 0, 0, 0), states[0])
        self.assertEqual(UpdateState(count - 1, count - 1, count - 1, count - 1, count - 1), states[-1])
        self.assertEqual(2, service.put_count)
