Pyrogram RockServer storage
======================

Usage
-----

```python

from pyrogram import Client
from pyrogram_rockserver_storage import RockServerStorage

session = RockServerStorage(hostname=..., port=..., session_unique_name=..., save_user_peers=...)
pyrogram = Client(session)
await pyrogram.connect()

```