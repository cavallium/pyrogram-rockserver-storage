import asyncio
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor

import grpc
from google.protobuf.wrappers_pb2 import BytesValue


def encode(value):
    return BytesValue(value=value).SerializeToString()


def decode(value):
    return BytesValue.FromString(value).value

from pyrogram_rockserver_storage import ResilientRpcClient


class Stub:
    def __init__(self, channel):
        self.call = channel.unary_unary('/test.Service/call', request_serializer=encode, response_deserializer=decode)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.servers = []
        self.clients = []
        self.entered = asyncio.Event()
        self.release = threading.Event()
        self.executors = []
        self.requests = []

    async def asyncTearDown(self):
        self.release.set()
        for client in self.clients:
            await client.close()
        for server in self.servers:
            server.stop(0).wait()

        for executor in self.executors:
            executor.shutdown(wait=True)

    async def server(self, port=0):
        loop = asyncio.get_running_loop()
        def call(request, context):
            self.requests.append(request)
            if request == b'fail':
                context.abort(grpc.StatusCode.UNAVAILABLE, 'injected failure')
            if request == b'hold':
                loop.call_soon_threadsafe(self.entered.set)
                while context.is_active() and not self.release.wait(0.01):
                    pass
            return request
        executor = ThreadPoolExecutor(max_workers=8)
        self.executors.append(executor)
        server = grpc.server(executor)
        server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
            'test.Service', {'call': grpc.unary_unary_rpc_method_handler(call, request_deserializer=decode, response_serializer=encode)}),))
        port = server.add_insecure_port(f'127.0.0.1:{port}')
        server.start()
        self.servers.append(server)
        return server, port

    def client(self, port, **kwargs):
        client = ResilientRpcClient('127.0.0.1', port, Stub,
                                    initial_backoff_ms=1, **kwargs)
        self.clients.append(client)
        return client

    async def test_failure_does_not_cancel_concurrent_rpc(self):
        _, port = await self.server()
        client = self.client(port)
        held = asyncio.create_task(client.call(b'hold'))
        await asyncio.wait_for(self.entered.wait(), 2)
        with self.assertRaises((grpc.aio.AioRpcError, ConnectionError)):
            await client.call(b'fail')
        self.assertFalse(held.done())
        self.release.set()
        self.assertEqual(await held, b'hold')

    async def test_default_deadline_bounds_unresponsive_server(self):
        _, port = await self.server()
        client = self.client(port, rpc_timeout=0.1)
        with self.assertRaises(grpc.aio.AioRpcError) as error:
            await asyncio.wait_for(client.call(b'hold'), 1)
        self.assertEqual(error.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        self.assertEqual(await client.call(b'ok'), b'ok')

    async def test_channel_recovers_after_real_server_outage(self):
        server, port = await self.server()
        client = self.client(port, rpc_timeout=3)
        self.assertEqual(await client.call(b'before'), b'before')
        channel = client._channel
        server.stop(0).wait()
        pending = asyncio.create_task(client.call(b'after'))
        await asyncio.sleep(0.1)
        await self.server(port)
        self.assertEqual(await pending, b'after')
        self.assertIs(client._channel, channel)

    async def test_close_cannot_resurrect_transport(self):
        _, port = await self.server()
        client = self.client(port)
        await client.close()
        with self.assertRaises(ConnectionError):
            await client.call(b'after-close')
        self.assertIsNone(client._channel)

    async def test_caller_cancellation_is_not_retried(self):
        _, port = await self.server()
        client = self.client(port)
        pending = asyncio.create_task(client.call(b'hold'))
        await asyncio.wait_for(self.entered.wait(), 2)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(await client.call(b'healthy'), b'healthy')

    async def test_deadline_includes_retry_sleep(self):
        _, port = await self.server()
        client = self.client(port)
        client._initial_backoff_ms = 1000
        with self.assertRaises(grpc.aio.AioRpcError) as error:
            await asyncio.wait_for(client.call(b'fail', timeout=0.1), 0.5)
        self.assertEqual(error.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)

    async def test_zero_deadline_is_not_unbounded(self):
        _, port = await self.server()
        client = self.client(port)
        with self.assertRaises(grpc.aio.AioRpcError) as error:
            await client.call(b'hold', timeout=0)
        self.assertEqual(error.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)

    async def test_default_deadline_includes_connection_lock(self):
        _, port = await self.server()
        client = self.client(port, rpc_timeout=0.05)
        async with client._lock:
            with self.assertRaises(grpc.aio.AioRpcError) as error:
                await asyncio.wait_for(client.call(b'blocked'), 0.5)
        self.assertEqual(error.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        self.assertIsNone(client._channel)
        self.assertEqual(await client.call(b'recovered'), b'recovered')

    async def test_close_cancels_inflight_call_without_reopening(self):
        _, port = await self.server()
        client = self.client(port)
        pending = asyncio.create_task(client.call(b'hold'))
        await asyncio.wait_for(self.entered.wait(), 2)
        await client.close()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 0.5)
        with self.assertRaises(ConnectionError):
            await client.call(b'closed')
        self.assertIsNone(client._channel)

    async def test_nonfinite_defaults_are_rejected(self):
        for timeout in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    self.client(1, rpc_timeout=timeout)

    async def test_nonfinite_explicit_deadlines_are_rejected(self):
        client = self.client(1)
        for timeout in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    await client.call(b'invalid', timeout=timeout)
        self.assertIsNone(client._channel)

    async def test_observer_failures_cannot_change_rpc_or_close(self):
        for raised in (RuntimeError, asyncio.CancelledError):
            with self.subTest(observer_failure=raised):
                _, port = await self.server()
                events = []
                def observe(event, value):
                    events.append((event, value))
                    raise raised()
                client = self.client(port, observer=observe)
                self.assertEqual(await client.call(b'ok'), b'ok')
                with self.assertRaises(grpc.aio.AioRpcError) as failure:
                    await client.call(b'fail')
                self.assertEqual(failure.exception.code(), grpc.StatusCode.UNAVAILABLE)
                self.entered.clear()
                held = asyncio.create_task(client.call(b'hold'))
                await asyncio.wait_for(self.entered.wait(), 2)
                held.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await held
                await client.close()
                self.assertIsNone(client._channel)
                with self.assertRaises(ConnectionError):
                    await client.call(b'closed')
                counts = {event: sum(value for name, value in events if name == event)
                          for event, _ in events}
                self.assertEqual(counts['calls'], 4)
                self.assertEqual(counts['attempts'], 5)
                self.assertEqual(counts['retries'], 2)
                self.assertEqual(counts['successes'], 1)
                self.assertEqual(counts['failures'], 2)
                self.assertEqual(counts['cancellations'], 1)
                self.assertEqual(counts['active_delta'], 0)
                self.assertEqual(counts['channels_created'], 1)
                self.assertEqual(counts['channels_closed'], 1)

    async def test_metrics_partition_deadline_and_measure_wait_without_extra_rpc(self):
        _, port = await self.server()
        events = []
        client = self.client(port, observer=lambda event, value: events.append((event, value)))
        client._initial_backoff_ms = 1000
        with self.assertRaises(grpc.aio.AioRpcError) as failure:
            await client.call(b'fail', timeout=0.1)
        self.assertEqual(failure.exception.code(), grpc.StatusCode.DEADLINE_EXCEEDED)
        counts = {event: sum(value for name, value in events if name == event)
                  for event, _ in events}
        self.assertEqual(counts['calls'], 1)
        self.assertEqual(counts['attempts'], 1)
        self.assertEqual(counts['retries'], 1)
        self.assertEqual(counts['deadlines'], 1)
        self.assertNotIn('failures', counts)
        self.assertEqual(counts['active_delta'], 0)
        self.assertGreater(counts['retry_wait_seconds'], 0)
        self.assertGreaterEqual(counts['call_seconds'], counts['retry_wait_seconds'])
        self.assertEqual(counts.get('ready_attempts', 0) + counts.get('not_ready_attempts', 0), 1)

    async def test_readiness_probe_failure_cannot_fail_rpc(self):
        from unittest.mock import patch
        for observer in (None, lambda event, value: None):
            with self.subTest(observed=observer is not None):
                _, port = await self.server()
                client = self.client(port, observer=observer)
                await client.connect()
                before = len(self.requests)
                with patch.object(client._channel, "get_state", side_effect=RuntimeError("probe failed")) as probe:
                    self.assertEqual(await client.call(b"ok"), b"ok")
                    self.assertEqual(probe.call_count, 0 if observer is None else 1)
                    self.assertEqual(len(self.requests) - before, 1)
