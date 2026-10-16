import unittest
from unittest.mock import AsyncMock, patch

import bson
import pyrogram

import pyrogram_rockserver_storage as storage
from pyrogram_rockserver_storage import RockServerStorage, UpdateState
from pyrogram_rockserver_storage import rocksdb_pb2 as pb


class SessionStore:
    """Persist BSON bytes at the same boundary used by the real transport."""
    def __init__(self):
        self.columns = {}
        self.rows = {}
        self.put_count = 0
        self.fail_put = False
        self.connect = AsyncMock()
        self.close = AsyncMock()

    async def createColumn(self, request):
        column = self.columns.setdefault(request.name, len(self.columns) + 1)
        return pb.CreateColumnResponse(columnId=column)

    async def deleteColumn(self, request):
        self.rows.pop(request.columnId, None)

    async def get(self, request):
        return pb.GetResponse(value=self.rows.get(request.columnId, b''))

    async def put(self, request):
        self.put_count += 1
        if self.fail_put:
            raise ConnectionError('storage unavailable')
        self.rows[request.columnId] = request.data.value


class StorageApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = SessionStore()
        self.factory = patch.object(storage, 'ResilientRpcClient', return_value=self.store)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.session = RockServerStorage('unused', 0, 'api-test', True)

    async def test_constructor_supports_both_storage_generations(self):
        self.assertEqual('api-test', self.session.name)
        self.assertFalse(self.session.__abstractmethods__)

    async def test_partial_states_merge_without_erasing_fields_or_rpc(self):
        await self.session.set_update_state(UpdateState(0, 42, 2, 50, 9))
        await self.session.set_update_state(UpdateState(0, 0, None, None, 0))
        self.assertEqual([UpdateState(0, 0, 2, 50, 0)], await self.session.get_update_states())
        self.assertEqual((0, 0, 2, 50, 0), self.session.get_cached_update_state(0))
        self.assertEqual(0, self.store.put_count)

    async def test_filters_deletes_iterables_empty_missing_and_date_order(self):
        states = [UpdateState(3, 1, None, 8, None), UpdateState(2, 1, None, None, None),
                  UpdateState(1, 1, None, 4, None)]
        await self.session.set_update_state(state for state in states)
        self.assertEqual([states[1], states[2], states[0]], await self.session.get_update_states())
        self.assertEqual([states[2], states[0]],
                         await self.session.get_update_states(i for i in (3, 1, 3, 99)))
        self.assertEqual([states[0]], await self.session.get_update_states(3))
        self.assertEqual([], await self.session.get_update_states(iter(())))
        self.assertEqual([], await self.session.get_update_states(99))
        await self.session.delete_update_state(i for i in (3, 99))
        await self.session.delete_update_state(2)
        await self.session.delete_update_state(2)
        self.assertEqual([states[2]], await self.session.get_update_states())

    async def test_legacy_replacement_and_new_partial_merge_share_state(self):
        await self.session.update_state([0, 4, 5, 8, 7])
        await self.session.set_update_state(UpdateState(0, 6, None, None, None))
        self.assertEqual([(0, 6, 5, 8, 7)], await self.session.update_state())
        await self.session.update_state((0, 1, None, 9, None))
        self.assertEqual([UpdateState(0, 1, None, 9, None)], await self.session.get_update_states())

    async def test_cursors_are_not_evicted_at_peer_cache_capacity(self):
        await self.session.set_update_state(UpdateState(i, i, None, i, None) for i in range(100_001))
        self.assertEqual((0, 0, None, 0, None), self.session.get_cached_update_state(0))
        self.assertEqual(100_001, len(await self.session.get_update_states()))

    async def test_save_close_reopen_merge_and_delete_roundtrip(self):
        await self.session.open()
        await self.session.set_update_state(UpdateState(0, 1, 2, 3, 4))
        await self.session.set_update_state(UpdateState(42, 5, None, 6, None))
        await self.session.save()
        self.assertEqual(1, self.store.put_count)
        self.assertEqual([[0, 1, 2, 3, 4], [42, 5, None, 6, None]],
                         bson.loads(self.store.rows[self.session._session_col])['update_states'])
        await self.session.close()
        reopened = RockServerStorage('unused', 0, 'api-test', True)
        await reopened.open()
        self.assertEqual((42, 5, None, 6, None), reopened.get_cached_update_state(42))
        await reopened.set_update_state(UpdateState(0, 7, None, None, None))
        await reopened.delete_update_state(42)
        await reopened.save()
        await reopened.close()
        await self.session.open()
        self.assertEqual([UpdateState(0, 7, 2, 3, 4)], await self.session.get_update_states())
        self.assertEqual(2, self.store.put_count)

    async def test_current_sdk_resumes_from_saved_cursor_and_persists_recovery(self):
        if not hasattr(storage.Storage, 'get_update_states'):
            self.skipTest('current SDK recovery integration')
        await self.session.open()
        await self.session.set_update_state(UpdateState(0, 42, 2, 123, 9))
        await self.session.save()
        await self.session.close()
        resumed = RockServerStorage('unused', 0, 'api-test', True)
        await resumed.open()
        client = pyrogram.Client('recovery-test', storage_engine=resumed)
        client.invoke = AsyncMock(return_value=pyrogram.raw.types.updates.DifferenceEmpty(
            date=200, seq=10))
        try:
            self.assertIs(resumed, client.storage)
            self.assertEqual((0, 0), await client.recover_gaps(0))
            request = client.invoke.call_args.args[0]
            self.assertIsInstance(request, pyrogram.raw.functions.updates.GetDifference)
            self.assertEqual(42, request.pts)
            self.assertEqual(123, request.date)
            client.invoke.assert_awaited_once()
        finally:
            client.executor.shutdown(wait=False)
            await resumed.close()
        await self.session.open()
        self.assertEqual([UpdateState(0, 42, 2, 200, 10)],
                         await self.session.get_update_states())
        await self.session.close()

    async def test_failed_save_preserves_pending_state_for_later_retry(self):
        await self.session.open()
        await self.session.set_update_state(UpdateState(0, 1, None, 2, None))
        self.store.fail_put = True
        with patch.object(storage.asyncio, 'sleep', new=AsyncMock()):
            with self.assertRaises(ConnectionError):
                await self.session.save()
        self.assertEqual((0, 1, None, 2, None), self.session.get_cached_update_state(0))
        self.store.fail_put = False
        await self.session.save()
        await self.session.close()
        await self.session.open()
        self.assertEqual([UpdateState(0, 1, None, 2, None)], await self.session.get_update_states())

    async def test_old_session_without_states_still_opens(self):
        self.store.columns['pyrogram_session_api-test'] = 1
        self.store.rows[1] = bson.dumps({'dc_id': 2, 'test_mode': False, 'user_id': 42})
        await self.session.open()
        self.assertEqual(42, await self.session.user_id())
        self.assertEqual('149.154.167.51', await self.session.server_address())
        self.assertEqual([], await self.session.get_update_states())

    async def test_delete_resets_session_cursors_and_peer_caches(self):
        await self.session.open()
        await self.session.user_id(42)
        await self.session.set_update_state(UpdateState(0, 1, None, 2, None))
        await self.session.update_usernames([(42, ['alias'])])
        self.session._phone_to_id['phone'] = 42
        await self.session.delete()
        self.assertIsNone(await self.session.user_id())
        self.assertEqual([], await self.session.get_update_states())
        self.assertEqual(0, len(self.session._phone_to_id))
        self.assertEqual(0, len(self.session._username_to_id))
        self.assertEqual(0, len(self.session._peer_usernames))
        await self.session.save()
        await self.session.close()
        await self.session.open()
        self.assertIsNone(await self.session.user_id())
        self.assertEqual([], await self.session.get_update_states())

    async def test_failed_or_cancelled_open_closes_transport(self):
        import asyncio
        for error in (ConnectionError('unavailable'), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                self.store.close.reset_mock()
                with patch.object(self.store, 'createColumn', new=AsyncMock(side_effect=error)):
                    with self.assertRaises(type(error)):
                        await self.session.open()
                self.store.close.assert_awaited_once()

    async def test_nullable_username_entries_and_community_peer_type(self):
        await self.session.update_usernames([(42, [None, 'Alias'])])
        self.assertEqual(frozenset({'alias'}), self.session._peer_usernames[42][0])
        peer = storage.get_input_peer({'id': -1000000000042, 'peer_type': 'community', 'access_hash': 3})
        self.assertEqual(42, peer.channel_id)
