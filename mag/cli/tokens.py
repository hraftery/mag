"""Manage mag API tokens: issue, list, edit, revoke.

Prints the raw token once at issuance - only its hash is stored
(api_tokens.json), so a lost token can only be revoked and reissued, not
recovered.

Not runnable on its own - invoked via mag's issue/list/edit/revoke
commands:

Usage:
    mag issue --name "laptop-explore" \\
              --scope "Sale/Invoice:GET" \\
              --scope "Banking/SpendMoneyTxn:GET" \\
              --scope "Contact:GET"
    mag list
    mag edit 7f9a2e1c --add-scope "Sale/Invoice:POST"
    mag revoke 7f9a2e1c
"""

import argparse
import sys

from mag.lib.token_store import add_scope, find_by_id, issue, load_records, revoke


def cmd_issue(args):
    try:
        raw_token, record = issue(args.name, args.scope)
    except ValueError as e:
        sys.exit(str(e))

    print(f"Issued token {record['id']} ({record['name']})")
    for scope in record["scopes"]:
        print(f"  scope: {scope['prefix'] or '*'} [{', '.join(scope['methods'])}]")
    print()
    print("Raw token (shown once - save it now):")
    print(raw_token) # Never stored


def cmd_list(args):
    records = load_records()
    if not records:
        print("No tokens issued yet.")
        return

    for r in records:
        status = "REVOKED" if r["revoked"] else "active"
        print(f"{r['id']}  {r['name']:<24} [{status}]")
        print(f"    scopes:     {_format_scopes(r['scopes'])}")
        print(f"    created:    {r['created_at']}")
        print(f"    last used:  {r['last_used_at'] or 'never'}")
        print()


def _format_scopes(scopes: list[dict]) -> str:
    return "; ".join(f"{s['prefix'] or '*'}:{','.join(s['methods'])}" for s in scopes)


def cmd_edit(args):
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


def cmd_revoke(args):
    record = find_by_id(args.token_id)
    if not record:
        sys.exit(f"No token with id {args.token_id}")

    if record["revoked"]:
        print(f"{args.token_id} ({record['name']}) is already revoked.")
        return

    revoke(args.token_id)
    print(f"Revoked {args.token_id} ({record['name']}).")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_issue = subparsers.add_parser("issue", help="Issue a new token")
    p_issue.add_argument("--name", required=True, help='Human-readable purpose, e.g. "gas-invoice-create"')
    p_issue.add_argument(
        "--scope",
        action="append",
        required=True,
        metavar="PREFIX:METHODS",
        help="e.g. Sale/Invoice:GET,POST - repeatable for multiple scopes",
    )
    p_issue.set_defaults(func=cmd_issue)

    p_list = subparsers.add_parser("list", help="List tokens and their scopes")
    p_list.set_defaults(func=cmd_list)

    p_edit = subparsers.add_parser(
        "edit",
        help="Add a scope to an existing token, without rotating its secret",
        description="Add a scope to an existing token, without rotating its secret or "
        "affecting its other scopes. To narrow a token, revoke it and issue a "
        "replacement instead - scopes can only be added here, not removed.",
    )
    p_edit.add_argument("token_id")
    p_edit.add_argument("--add-scope", required=True, metavar="PREFIX:METHODS")
    p_edit.set_defaults(func=cmd_edit)

    p_revoke = subparsers.add_parser("revoke", help="Revoke a token by id")
    p_revoke.add_argument("token_id")
    p_revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit("Run this via: mag <issue|list|edit|revoke>  (see setup.sh)")
