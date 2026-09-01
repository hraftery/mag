"""Issuance and validation of mag's own bearer tokens - the credentials
handed to clients (GAS, local scripts, Postman) so they never need to hold
or negotiate a real MYOB credential. Entirely separate from tokens.json
(MYOB's own OAuth tokens), which only proxy.py / myob_client.py ever touch.

See README: "Client access: token issuer + thin proxy".

Only a token's hash is ever stored on disk - the raw value is shown once at
issuance (by `mag issue`) and cannot be recovered, only reissued.
"""

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone

from mag.lib.paths import DATA_DIR

MAG_TOKENS_FILE = os.path.join(DATA_DIR, "mag_tokens.json")

TOKEN_PREFIX = "mbt_"  # grep-able marker, same idea as GitHub/Stripe token prefixes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load_records() -> list[dict]:
    if not os.path.exists(MAG_TOKENS_FILE):
        return []
    with open(MAG_TOKENS_FILE) as f:
        return json.load(f)


def save_records(records: list[dict]) -> None:
    # dirname(MAG_TOKENS_FILE), not DATA_DIR, so tests can redirect MAG_TOKENS_FILE.
    os.makedirs(os.path.dirname(MAG_TOKENS_FILE), exist_ok=True)
    with open(MAG_TOKENS_FILE, "w") as f:
        json.dump(records, f, indent=2)
    try:
        # chmod requires *owning* the file, not just group membership. Since
        # this file is written by `mag issue/edit/revoke` *and* by
        # mag-proxy.service we may end up with a different owner. So we rely
        # on umask instead (see setup.sh's mag wrapper and mag-proxy.service)
        # and this is just a backup that may fail.
        os.chmod(MAG_TOKENS_FILE, 0o660)
    except PermissionError:
        pass


def parse_scope(spec: str) -> dict:
    """Parse "prefix:METHOD1,METHOD2" into {"prefix": ..., "methods": [...]}.
    An empty prefix ("" or ":GET") matches every path."""
    if ":" not in spec:
        raise ValueError(f'Scope {spec!r} must be "prefix:METHOD1,METHOD2"')
    prefix, methods = spec.split(":", 1)
    method_list = [m.strip().upper() for m in methods.split(",") if m.strip()]
    if not method_list:
        raise ValueError(f"Scope {spec!r} has no methods")
    return {"prefix": prefix.strip("/"), "methods": method_list}


def issue(name: str, scope_specs: list[str]) -> tuple[str, dict]:
    """Create a new token. Returns (raw_token, record)."""
    raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = {
        "id": secrets.token_hex(4),
        "name": name,
        "token_hash": _hash(raw_token),
        "scopes": [parse_scope(s) for s in scope_specs],
        "created_at": _now(),
        "last_used_at": None,
        "revoked": False,
    }
    records = load_records()
    records.append(record)
    save_records(records)
    return raw_token, record


def find_by_id(token_id: str) -> dict | None:
    return next((r for r in load_records() if r["id"] == token_id), None)


def revoke(token_id: str) -> bool:
    records = load_records()
    record = next((r for r in records if r["id"] == token_id), None)
    if not record:
        return False
    record["revoked"] = True
    save_records(records)
    return True


def add_scope(token_id: str, scope_spec: str) -> bool:
    records = load_records()
    record = next((r for r in records if r["id"] == token_id), None)
    if not record:
        return False
    record["scopes"].append(parse_scope(scope_spec))
    save_records(records)
    return True


def _path_matches(path: str, prefix: str) -> bool:
    """Segment-boundary match: "Sale/Invoice" matches "Sale/Invoice/Item" but
    NOT "Sale/InvoiceTemplate" - a naive startswith() would wrongly allow
    the latter. An empty prefix matches everything."""
    prefix = prefix.strip("/")
    if prefix == "":
        return True
    path_parts = path.strip("/").split("/")
    prefix_parts = prefix.split("/")
    return path_parts[: len(prefix_parts)] == prefix_parts


def authorize(raw_token: str, method: str, path: str) -> dict | None:
    """Validate a bearer token against a request's method + path. Returns
    the token record on success (and records last_used_at), or None if the
    token is unknown, revoked, or lacks a matching scope."""
    token_hash = _hash(raw_token)
    records = load_records()
    record = next(
        (r for r in records if hmac.compare_digest(r["token_hash"], token_hash)), None
    )
    if not record or record["revoked"]:
        return None

    allowed = any(
        _path_matches(path, scope["prefix"]) and method.upper() in scope["methods"]
        for scope in record["scopes"]
    )
    if not allowed:
        return None

    record["last_used_at"] = _now()
    save_records(records)
    return record
