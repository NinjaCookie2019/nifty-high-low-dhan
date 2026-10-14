"""Persist renewed credentials privately on the Railway volume, without redeploys."""
import base64
import json
import os
from pathlib import Path


def token_expiry(token):
    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["exp"])
    except (ValueError, KeyError, IndexError, TypeError):
        return 0


def load_token(path, environment_token, client_id):
    """Use the newer token; JWT metadata selects candidates, never authenticates them."""
    try:
        data = Path(path).read_text()
        cached = json.loads(data)
        if cached.get("client_id") == client_id:
            token = cached.get("access_token", "")
            if token_expiry(token) > token_expiry(environment_token):
                return token
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return environment_token


def save_token(path, token, client_id):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump({"access_token": token, "client_id": client_id}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
        return True, None
    except OSError as exc:
        return False, f"Credential cache write failed ({type(exc).__name__})"
