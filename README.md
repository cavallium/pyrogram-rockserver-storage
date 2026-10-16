Pyrogram RockServer storage
======================

Usage
-----

```python

from pyrogram import Client
from pyrogram_rockserver_storage import RockServerStorage

session = RockServerStorage(hostname=..., port=..., session_unique_name=..., save_user_peers=...)
pyrogram = Client("account", storage_engine=session)
await pyrogram.connect()

```

Requires Python 3.10 or newer. Supports the older tuple-based Pyrogram storage
interface and the current `UpdateState` interface. Update states remain in memory
between saves and are persisted in the session record by `save()` (including
SDK startup, gap-recovery, and graceful-termination saves). Unsaved updates are
lost on an abrupt stop. Peer and username caches keep their existing behavior.

Saved session records, including authentication and update cursors, are limited
to 64 MiB. Larger saves fail before writing and retain the previous saved session;
pending changes remain in memory. The receive limit includes the protobuf response
envelope so every accepted snapshot can be read again.
