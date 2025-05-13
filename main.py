import logging

from pyrogram_rockserver_storage import RockServerStorage
import asyncio

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(message)s")

async def main():
    print("ciaoA")
    await storage.open()
    print("ciaoB")
    await storage.update_peers([(-1001946993950, 2, "channel", "4", "5"), (-6846993950, 2, "group", "4", "5")])
    print("ciaoC")
    print("Peer channel", await storage.get_peer_by_id(-1001946993950))
    print("Peer group", await storage.get_peer_by_id(-6846993950))
    print("dc_id", await storage.dc_id())
    await storage.dc_id(8)
    print("dc_id", await storage.dc_id())

print("ciao")
storage = RockServerStorage(save_user_peers=False, hostname="127.0.0.1", port=5332, session_unique_name="test_99")
print("ciao2")
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
print("ciao3")
