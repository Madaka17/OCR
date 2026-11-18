import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SESSION_SECONDS = 30 * 86400


def _scrypt(pw: str, salt: bytes, dklen: int) -> bytes:
    return hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=dklen)


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    return f"scrypt${salt.hex()}${_scrypt(pw, salt, 64).hex()}"


def check_password(pw: str, stored: str | None) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (AttributeError, ValueError):
        return False
    if scheme != "scrypt" or not expected:
        return False
    return hmac.compare_digest(_scrypt(pw, salt, len(expected)), expected)


class Auth:
    """Shared user and admin passwords plus HMAC-signed session cookies.

    Cookie format: "<hex of JSON payload>.<hex HMAC-SHA256 of that hex string>".
    The payload carries role, owner and exp (unix seconds).
    """

    def __init__(self, config_path):
        self.path = Path(config_path)
        self._lock = threading.Lock()
        self._config = self._load()
        if not self._config.get("secret"):
            self._config["secret"] = secrets.token_hex(32)
            self._save()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        """Write config.json atomically: temp file, fsync, then replace, so a crash never leaves it half written."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps(self._config, indent=2).encode("utf-8")
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    def set_passwords(self, user_pw: str, admin_pw: str) -> None:
        """Set both passwords. A new secret is generated, so every existing session stops working."""
        if not user_pw or not admin_pw:
            raise ValueError("passwords must not be empty")
        self._config["user"] = hash_password(user_pw)
        self._config["admin"] = hash_password(admin_pw)
        self._config["secret"] = secrets.token_hex(32)
        self._save()

    def ready(self) -> bool:
        return bool(self._config.get("user") and self._config.get("admin"))

    def login(self, pw: str, old_cookie: str | None) -> tuple[str, dict] | None:
        if check_password(pw, self._config.get("admin")):
            role = "admin"
        elif check_password(pw, self._config.get("user")):
            role = "user"
        else:
            return None
        owner = None
        old = self.session(old_cookie) if old_cookie else None
        if old is not None:
            owner = old["owner"]
        session = {
            "role": role,
            "owner": owner or secrets.token_urlsafe(16),
            "exp": int(time.time()) + SESSION_SECONDS,
        }
        return self._sign(session), session

    def session(self, cookie_value: str | None) -> dict | None:
        if not cookie_value or "." not in cookie_value:
            return None
        payload_hex, _, sig = cookie_value.rpartition(".")
        expected = self._mac(payload_hex)
        if not hmac.compare_digest(sig.encode("utf-8"), expected.encode("utf-8")):
            return None
        try:
            data = json.loads(bytes.fromhex(payload_hex).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        if data.get("role") not in ("user", "admin") or not isinstance(data.get("owner"), str):
            return None
        if not isinstance(data.get("exp"), (int, float)) or data["exp"] <= time.time():
            return None
        return data

    def _mac(self, payload_hex: str) -> str:
        key = bytes.fromhex(self._config["secret"])
        return hmac.new(key, payload_hex.encode("utf-8"), hashlib.sha256).hexdigest()

    def _sign(self, session: dict) -> str:
        payload_hex = json.dumps(session, separators=(",", ":")).encode("utf-8").hex()
        return f"{payload_hex}.{self._mac(payload_hex)}"
