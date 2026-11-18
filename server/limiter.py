"""In-memory brake on password guessing: per client key and across all clients.

A key is blocked once it has KEY_LIMIT failed logins inside KEY_WINDOW seconds. Across
all keys, the server stops accepting logins while more than GLOBAL_LIMIT failures fall
inside GLOBAL_WINDOW seconds. A successful login clears that key's failures.
"""
import threading
import time
from collections import deque

KEY_LIMIT = 5
KEY_WINDOW = 15 * 60
GLOBAL_LIMIT = 30
GLOBAL_WINDOW = 60


class LoginLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, deque] = {}
        self._all: deque = deque()

    def _prune(self, now: float) -> None:
        key_cutoff = now - KEY_WINDOW
        for key in list(self._keys):
            failures = self._keys[key]
            while failures and failures[0] <= key_cutoff:
                failures.popleft()
            if not failures:
                del self._keys[key]
        all_cutoff = now - GLOBAL_WINDOW
        while self._all and self._all[0] <= all_cutoff:
            self._all.popleft()

    def allowed(self, key: str) -> bool:
        """False while the key or every client is blocked. Does not record anything."""
        with self._lock:
            self._prune(self._clock())
            if len(self._all) > GLOBAL_LIMIT:
                return False
            failures = self._keys.get(key)
            return not failures or len(failures) < KEY_LIMIT

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._all.append(now)
            self._keys.setdefault(key, deque()).append(now)

    def record_success(self, key: str) -> None:
        with self._lock:
            self._keys.pop(key, None)
