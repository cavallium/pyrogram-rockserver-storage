from pyrogram_rockserver_storage import RockServerStorage
import asyncio


async def main():
    await storage.open()
    await storage.update_peers([(-1001946993950, 2, "channel", "4", "5"), (-6846993950, 2, "group", "4", "5")])
    print("Peer channel", await storage.get_peer_by_id(-1001946993950))
    print("Peer group", await storage.get_peer_by_id(-6846993950))
    print("dc_id", await storage.dc_id())
    await storage.dc_id(4)

print("ciao")
storage = RockServerStorage(save_user_peers=False, hostname="127.0.0.1", port=5332, session_unique_name="test_99")
print("ciao2")
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
print("ciao3")
