import sys
from typing import Tuple, List

from pyrogram_rockserver_storage import RockServerStorage
import asyncio
import json

slaveId = sys.argv[1]

async def main():
    await storage.open()

    # Reading from a JSON Lines file
    with open(f"pyrogram_session_dtg-slave-{slaveId}.json", 'r') as file:
        for line in file:
            data_entry = json.loads(line)
            # Process each data_entry as a Python dict
            print(data_entry)
            await storage.dc_id(data_entry['dc_id'])
            await storage.api_id(data_entry['api_id'])
            await storage.test_mode(data_entry['test_mode'])
            await storage.auth_key(bytes.fromhex(data_entry['auth_key'][2:]))
            await storage.date(data_entry['date'])
            await storage.user_id(data_entry['user_id'])
            await storage.is_bot(data_entry['is_bot'])
    progress = 0
    def f() -> List[Tuple[int, int, str, str, str]]:
        return []
    def ff(a: int, b: int, c: str, d: str, e: str, f: int) -> Tuple[int, int, str, str, str]:
        return (a, b, c, d, e, f)
    # Reading from a JSON Lines file
    with open(f"peers_dtg-slave-{slaveId}.json", 'r') as file:
        buf: List[Tuple[int, int, str, str, str]] = f()
        for line in file:
            progress += 1
            data_entry = json.loads(line)
            buf.append(ff(data_entry['id'], data_entry['access_hash'],
                        data_entry['type'], data_entry['username'],
                        data_entry['phone_number'], data_entry['last_update_on']))
            if (len(buf) > 10000):
                await storage.update_peers(buf)
                buf.clear()
            if progress % 10000 == 0:
                print(f"Progress: {progress}")
        if (len(buf) > 0):
            await storage.update_peers(buf)
            buf.clear()
    exit(1)


print("initializing rockserver storage")
storage = RockServerStorage(save_user_peers=False, hostname="db.local", port=5332, session_unique_name=f"dtg-slave-{slaveId}")
print("initialized rockserver storage")
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
print("done")
