#!/usr/bin/env python3
"""List mag API tokens and their scopes. Never prints the raw token or
its hash - only what's needed to decide whether to revoke or extend one.

Usage:
    python3 scripts/list_tokens.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from token_store import load_records


def format_scopes(scopes: list[dict]) -> str:
    return "; ".join(f"{s['prefix'] or '*'}:{','.join(s['methods'])}" for s in scopes)


def main():
    records = load_records()
    if not records:
        print("No tokens issued yet.")
        return

    for r in records:
        status = "REVOKED" if r["revoked"] else "active"
        print(f"{r['id']}  {r['name']:<24} [{status}]")
        print(f"    scopes:     {format_scopes(r['scopes'])}")
        print(f"    created:    {r['created_at']}")
        print(f"    last used:  {r['last_used_at'] or 'never'}")
        print()


if __name__ == "__main__":
    main()
