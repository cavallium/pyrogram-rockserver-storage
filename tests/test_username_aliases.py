import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from lru import LRU
import pyrogram_rockserver_storage as storage


class UsernameAliasTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1_000_000
        self.clock = patch.object(storage.time, 'time', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.storage = storage.RockServerStorage('unused', 0, 'alias-test', True)
        self.storage._peer_col = 7
        self.peer_id = -1000000000042
        self.other_id = -1000000000043
        self.read = AsyncMock(side_effect=lambda *args: self.row())
        self.reader = patch.object(storage, 'fetchone', new=self.read)
        self.reader.start()
        self.addCleanup(self.reader.stop)

    def row(self, updated_at=None):
        return storage.encode_peer_info(91, 'channel', None,
            int(self.now) if updated_at is None else updated_at)

    async def test_rename_replaces_old_alias_without_extra_storage_read(self):
        await self.storage.update_usernames([(self.peer_id, ['old'])])
        await self.storage.update_usernames([(self.peer_id, ['new'])])
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('old')
        self.read.assert_not_awaited()
        peer = await self.storage.get_peer_by_username('new')
        self.assertEqual(42, peer.channel_id)
        self.read.assert_awaited_once_with(None, 7,
            [self.peer_id.to_bytes(8, byteorder='big', signed=True)])

    async def test_empty_snapshot_removes_every_old_alias(self):
        await self.storage.update_usernames([(self.peer_id, ['primary', 'additional'])])
        await self.storage.update_usernames([(self.peer_id, [])])
        for username in ('primary', 'additional'):
            with self.assertRaises(KeyError):
                await self.storage.get_peer_by_username(username)
        self.read.assert_not_awaited()
        self.assertNotIn(self.peer_id, self.storage._peer_usernames)

    async def test_old_peer_replacement_preserves_alias_reassigned_to_new_owner(self):
        await self.storage.update_usernames([(self.peer_id, ['shared', 'old'])])
        await self.storage.update_usernames([(self.other_id, ['shared'])])
        await self.storage.update_usernames([(self.peer_id, [])])
        peer = await self.storage.get_peer_by_username('shared')
        self.assertEqual(43, peer.channel_id)
        self.assertEqual(self.other_id, self.storage._username_to_id['shared'])
        self.read.assert_awaited_once_with(None, 7,
            [self.other_id.to_bytes(8, byteorder='big', signed=True)])

    async def test_all_current_aliases_normalize_case_and_keep_one_read_fast_path(self):
        await self.storage.update_usernames([(self.peer_id, ['Primary', 'AdDiTiOnAl', 'primary'])])
        for username in ('PRIMARY', 'additional', 'PrImArY'):
            self.read.reset_mock()
            peer = await self.storage.get_peer_by_username(username)
            self.assertEqual(42, peer.channel_id)
            self.read.assert_awaited_once()
        self.assertEqual(frozenset(('primary', 'additional')), self.storage._peer_usernames[self.peer_id][0])
        self.assertEqual(100_000, self.storage._peer_usernames.get_size())
        self.assertEqual(100_000, self.storage._username_to_id.get_size())

    async def test_reverse_proof_eviction_is_miss_even_if_forward_binding_remains(self):
        self.storage._peer_usernames = LRU(1)
        await self.storage.update_usernames([(self.peer_id, ['old'])])
        await self.storage.update_usernames([(self.other_id, ['other'])])
        self.assertEqual(self.peer_id, self.storage._username_to_id['old'])
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('old')
        self.read.assert_not_awaited()
        await self.storage.update_usernames([(self.peer_id, ['new'])])
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('old')
        self.assertEqual(42, (await self.storage.get_peer_by_username('new')).channel_id)

    async def test_reassignment_during_fetch_never_returns_previous_peer_or_adds_read(self):
        await self.storage.update_usernames([(self.peer_id, ['shared'])])
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(*args):
            entered.set()
            await release.wait()
            return self.row()
        self.read.side_effect = read
        pending = asyncio.create_task(self.storage.get_peer_by_username('shared'))
        await entered.wait()
        await self.storage.update_usernames([(self.other_id, ['shared'])])
        release.set()
        with self.assertRaises(KeyError):
            await pending
        self.assertEqual(1, self.read.await_count)

    async def test_current_alias_proof_is_rechecked_after_fetch(self):
        self.storage._peer_usernames = LRU(1)
        await self.storage.update_usernames([(self.peer_id, ['shared'])])
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(*args):
            entered.set()
            await release.wait()
            return self.row()
        self.read.side_effect = read
        pending = asyncio.create_task(self.storage.get_peer_by_username('shared'))
        await entered.wait()
        await self.storage.update_usernames([(self.other_id, ['different'])])
        self.assertEqual(self.peer_id, self.storage._username_to_id['shared'])
        release.set()
        with self.assertRaises(KeyError):
            await pending
        self.assertEqual(1, self.read.await_count)

    async def test_fresh_peer_row_cannot_extend_expired_alias_confirmation(self):
        await self.storage.update_usernames([(self.peer_id, ['current'])])
        self.now += self.storage.USERNAME_TTL
        self.assertEqual(42, (await self.storage.get_peer_by_username('current')).channel_id)
        self.read.reset_mock()
        self.now += 1
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('current')
        self.read.assert_not_awaited()
        self.assertEqual(8 * 60 * 60, self.storage.USERNAME_TTL)
        await self.storage.update_usernames([(self.peer_id, ['current'])])
        self.assertEqual(42, (await self.storage.get_peer_by_username('current')).channel_id)
        self.read.assert_awaited_once()

    async def test_fresh_alias_confirmation_does_not_extend_old_peer_row_ttl(self):
        await self.storage.update_usernames([(self.peer_id, ['current'])])
        self.read.side_effect = lambda *args: self.row(int(self.now) - self.storage.USERNAME_TTL - 1)
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('current')
        self.read.assert_awaited_once()

    async def test_alias_confirmation_expiring_while_fetch_waits_is_rejected(self):
        await self.storage.update_usernames([(self.peer_id, ['current'])])
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(*args):
            entered.set()
            await release.wait()
            return self.row()
        self.read.side_effect = read
        pending = asyncio.create_task(self.storage.get_peer_by_username('current'))
        await entered.wait()
        self.now += self.storage.USERNAME_TTL + 1
        release.set()
        with self.assertRaises(KeyError):
            await pending
        self.assertEqual(1, self.read.await_count)

    async def test_peer_row_schema_is_unchanged(self):
        await self.storage.update_usernames([(self.peer_id, ['current'])])
        self.assertEqual({'access_hash', 'peer_type', 'phone_number', 'last_update_on'}, set(self.row()))
        self.assertEqual(42, (await self.storage.get_peer_by_username('current')).channel_id)

    async def test_empty_snapshots_do_not_evict_useful_named_peer_proof(self):
        self.storage._peer_usernames = LRU(1)
        await self.storage.update_usernames([(self.peer_id, ['current'])])
        for peer_id in range(100):
            await self.storage.update_usernames([(peer_id, [])])
        self.assertEqual(1, len(self.storage._peer_usernames))
        self.assertEqual(42, (await self.storage.get_peer_by_username('current')).channel_id)
        self.read.assert_awaited_once()

    async def test_numeric_cache_telemetry_counts_outcomes_and_reason_subsets(self):
        events = []
        allowed = {'username_cache_hits', 'username_cache_misses', 'username_cache_expired',
            'username_cache_changed_during_read', 'username_cache_invalidations', 'username_cache_snapshots'}
        def observe(event, value):
            events.append((event, value))
        self.storage._rpc_observer = observe
        await self.storage.update_usernames([(self.peer_id, ['old', 'shared'])])
        await self.storage.update_usernames([(self.other_id, ['shared'])])
        await self.storage.update_usernames([(self.peer_id, ['new'])])
        await self.storage.get_peer_by_username('new')
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('old')
        self.now += self.storage.USERNAME_TTL + 1
        with self.assertRaises(KeyError):
            await self.storage.get_peer_by_username('new')
        for event, value in events:
            self.assertIn(event, allowed)
            self.assertIs(type(value), int)
        totals = {name: sum(value for event, value in events if event == name) for name in allowed}
        self.assertEqual(3, totals['username_cache_snapshots'])
        self.assertEqual(1, totals['username_cache_invalidations'])
        self.assertEqual(1, totals['username_cache_hits'])
        self.assertEqual(2, totals['username_cache_misses'])
        self.assertEqual(1, totals['username_cache_expired'])
        self.assertEqual(0, totals['username_cache_changed_during_read'])
        self.assertEqual(1, self.read.await_count)

    async def test_read_reassignment_records_miss_and_changed_reason_without_false_hit(self):
        events = []
        self.storage._rpc_observer = lambda event, value: events.append((event, value))
        await self.storage.update_usernames([(self.peer_id, ['shared'])])
        entered, release = asyncio.Event(), asyncio.Event()
        async def read(*args):
            entered.set()
            await release.wait()
            return self.row()
        self.read.side_effect = read
        pending = asyncio.create_task(self.storage.get_peer_by_username('shared'))
        await entered.wait()
        await self.storage.update_usernames([(self.other_id, ['shared'])])
        release.set()
        with self.assertRaises(KeyError):
            await pending
        self.assertEqual(1, events.count(('username_cache_misses', 1)))
        self.assertEqual(1, events.count(('username_cache_changed_during_read', 1)))
        self.assertNotIn(('username_cache_hits', 1), events)
        self.assertEqual(1, self.read.await_count)

    async def test_hostile_cache_observer_preserves_success_removal_misses_and_cancellation(self):
        for error_type in (RuntimeError, asyncio.CancelledError):
            with self.subTest(error_type=error_type):
                def hostile(*args):
                    raise error_type('observer only')
                self.storage._rpc_observer = hostile
                self.read.side_effect = lambda *args: self.row()
                await self.storage.update_usernames([(self.peer_id, ['current'])])
                self.assertEqual(42, (await self.storage.get_peer_by_username('current')).channel_id)
                await self.storage.update_usernames([(self.peer_id, [])])
                with self.assertRaises(KeyError):
                    await self.storage.get_peer_by_username('current')
                self.assertNotIn('current', self.storage._username_to_id)
                await self.storage.update_usernames([(self.peer_id, ['current'])])
                self.read.side_effect = asyncio.CancelledError('actual caller cancellation')
                with self.assertRaises(asyncio.CancelledError):
                    await self.storage.get_peer_by_username('current')
