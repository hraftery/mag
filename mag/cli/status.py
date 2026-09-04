"""Show mag's current status: the mag-proxy service state, its recent
logs, MYOB authorisation, issued API tokens, and recent proxy activity.

Meant to be run on the server, but degrades gracefully if run elsewhere.

Not runnable on its own - invoked via mag's "status" command:
    mag status
"""

import os
import subprocess
import sys

from mag.lib import token_store
from mag.lib.myob_client import MYOB_TOKENS_FILE
from mag.proxy.proxy import AUDIT_LOG

SERVICE = "mag-proxy.service"
AUDIT_LOG_TAIL = 10


def _run(cmd: list[str]) -> str | None:
    """Run a read-only, informational command. Returns its combined output,
    or None if the command itself isn't installed (e.g. running this
    locally, off the server, where systemctl/journalctl don't exist)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except FileNotFoundError:
        return None
    return (result.stdout + result.stderr).strip()


def _section(title: str) -> None:
    print(f"\n== {title} ==")


def main():
    _section("mag-proxy service")
    output = _run(["systemctl", "status", SERVICE, "--no-pager", "-l"])
    if output is None:
        print("systemctl not found - not running on a systemd host?")
    else:
        print(output or "(no output)")

    _section("Recent logs")
    output = _run(["journalctl", "-u", SERVICE, "-n", "20", "--no-pager"])
    if output is None:
        print("journalctl not found - not running on a systemd host?")
    else:
        print(output or "(no logs yet)")

    _section("MYOB authorisation")
    if os.path.exists(MYOB_TOKENS_FILE):
        print(f"tokens.json present ({MYOB_TOKENS_FILE})")
    else:
        print(f"Not authorised yet - {MYOB_TOKENS_FILE} doesn't exist. Run `mag oauth`.")

    _section("API tokens issued")
    records = token_store.load_records()
    active = [r for r in records if not r["revoked"]]
    revoked = len(records) - len(active)
    print(f"{len(active)} active, {revoked} revoked. See `mag list` for details.")

    _section(f"Recent proxy activity (last {AUDIT_LOG_TAIL} lines)")
    if os.path.exists(AUDIT_LOG):
        with open(AUDIT_LOG) as f:
            lines = f.readlines()[-AUDIT_LOG_TAIL:]
        print("".join(lines).rstrip() or "(empty)")
    else:
        print("No requests logged yet.")


if __name__ == "__main__":
    sys.exit("Run this via: mag status (see setup.sh)")
