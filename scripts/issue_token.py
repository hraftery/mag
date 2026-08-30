#!/usr/bin/env python3
"""Issue a new mag API token.

Prints the raw token once - only its hash is stored (api_tokens.json), so if
it's lost the only option is to revoke it (scripts/revoke_token.py) and
issue a new one, not recover it.

Usage:
    python3 scripts/issue_token.py --name "laptop-explore" \\
        --scope "Sale/Invoice:GET" \\
        --scope "Banking/SpendMoneyTxn:GET" \\
        --scope "Contact:GET"
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
from token_store import issue


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help='Human-readable purpose, e.g. "gas-invoice-create"')
    parser.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="PREFIX:METHODS",
        help="e.g. Sale/Invoice:GET,POST - repeatable for multiple scopes",
    )
    args = parser.parse_args()

    try:
        raw_token, record = issue(args.name, args.scope)
    except ValueError as e:
        sys.exit(str(e))

    print(f"Issued token {record['id']} ({record['name']})")
    for scope in record["scopes"]:
        print(f"  scope: {scope['prefix'] or '*'} [{', '.join(scope['methods'])}]")
    print()
    print("Raw token (shown once - save it now):")
    print(raw_token)


if __name__ == "__main__":
    main()
