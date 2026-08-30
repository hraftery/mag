#!/usr/bin/env python3
"""Add a scope to an existing myobot API token, without rotating its secret
or affecting its other scopes. To narrow a token, revoke it and issue a
replacement instead - scopes can only be added here, not removed, since
removal has the same "did I get this right" risk as a fresh issuance
without the benefit of a clean audit trail.

Usage:
    python3 scripts/edit_token.py 7f9a2e1c --add-scope "Sale/Invoice:POST"
"""

import argparse
import sys

from token_store import add_scope, find_by_id


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("token_id")
    parser.add_argument("--add-scope", required=True, metavar="PREFIX:METHODS")
    args = parser.parse_args()

    record = find_by_id(args.token_id)
    if not record:
        sys.exit(f"No token with id {args.token_id}")
    if record["revoked"]:
        sys.exit(f"{args.token_id} ({record['name']}) is revoked - issue a new token instead.")

    try:
        add_scope(args.token_id, args.add_scope)
    except ValueError as e:
        sys.exit(str(e))

    print(f"Added scope {args.add_scope} to {args.token_id} ({record['name']}).")


if __name__ == "__main__":
    main()
