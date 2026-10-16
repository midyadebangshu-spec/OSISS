"""Identity of this API process among several copies (REPLICA_ID, or host name + process id)."""

from __future__ import annotations

import os
import socket
from functools import lru_cache

from config import settings


@lru_cache(maxsize=1)
def replica_id() -> str:
    return settings.replica_id or f"{socket.gethostname()}-{os.getpid()}"
