#!/usr/bin/env python3
"""Revoke a mag API token by id (see scripts/list_tokens.py for ids).

Takes effect immediately on the next request through the proxy. Has no
effect on MYOB's own OAuth grant - only on what this one token can reach.

Usage:
    python3 scripts/revoke_token.py 7f9a2e1c
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from token_store import find_by_id, revoke


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: revoke_token.py <token-id>")
    token_id = sys.argv[1]

    record = find_by_id(token_id)
    if not record:
        sys.exit(f"No token with id {token_id}")

    if record["revoked"]:
        print(f"{token_id} ({record['name']}) is already revoked.")
        return

    revoke(token_id)
    print(f"Revoked {token_id} ({record['name']}).")


if __name__ == "__main__":
    main()
