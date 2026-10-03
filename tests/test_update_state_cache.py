import unittest

from pyrogram_rockserver_storage import RockServerStorage


class UpdateStateCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = RockServerStorage('unused', 0, 'state-test', True)
        self.peer_id = -1000000000042

    async def test_channel_lookup_does_not_use_global_or_other_account_state(self):
        global_state = (0, 900, 5, 1000, 7)
        channel_state = (self.peer_id, 123, None, 1001, 7)
        await self.storage.update_state(global_state)
        await self.storage.update_state(channel_state)
        self.assertEqual(channel_state, self.storage.get_cached_update_state(self.peer_id))
        self.assertEqual(global_state, self.storage.get_cached_update_state(0))
        self.assertIsNone(self.storage.get_cached_update_state(self.peer_id - 1))
        other = RockServerStorage('unused', 0, 'another-account', True)
        self.assertIsNone(other.get_cached_update_state(self.peer_id))

    async def test_lookup_reads_one_entry_without_enumerating_the_cache(self):
        class NoEnumeration(dict):
            def values(self):
                raise AssertionError('scalar lookup must not enumerate update states')

            def __iter__(self):
                raise AssertionError('scalar lookup must not iterate update states')

        state = (self.peer_id, 123, None, 1001, 7)
        self.storage._update_to_state = NoEnumeration({self.peer_id: state})
        self.assertEqual(state, self.storage.get_cached_update_state(self.peer_id))

    async def test_lookup_returns_an_immutable_snapshot_and_reflects_replacements(self):
        mutable = [self.peer_id, 123, None, 1001, 7]
        await self.storage.update_state(mutable)
        snapshot = self.storage.get_cached_update_state(self.peer_id)
        mutable[1] = 124
        self.assertEqual(123, snapshot[1])
        replacement = (self.peer_id, 200, None, 1002, 8)
        await self.storage.update_state(replacement)
        self.assertEqual(replacement, self.storage.get_cached_update_state(self.peer_id))
        await self.storage.update_state(self.peer_id)
        self.assertIsNone(self.storage.get_cached_update_state(self.peer_id))
