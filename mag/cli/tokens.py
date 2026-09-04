"""Manage mag API tokens: issue, list, edit, revoke.

Prints the raw token once at issuance - only its hash is stored
(mag_tokens.json), so a lost token can only be revoked and reissued, not
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
    mag edit 7f9a2e1c --remove-scope "Sale/Invoice:GET,POST"
    mag revoke 7f9a2e1c
"""

import argparse
import sys

from mag.lib.token_store import add_scope, find_by_id, issue, load_records, remove_scope, revoke


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
        print(f"id: {r['id']}  name: {r['name']:<24} [{status}]")
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
    if not args.add_scope and not args.remove_scope:
        sys.exit("Specify --add-scope and/or --remove-scope.")

    try:
        if args.add_scope:
            add_scope(args.token_id, args.add_scope)
            print(f"Added scope {args.add_scope} to {args.token_id} ({record['name']}).")
        if args.remove_scope:
            remove_scope(args.token_id, args.remove_scope)
            print(f"Removed scope {args.remove_scope} from {args.token_id} ({record['name']}).")
    except ValueError as e:
        sys.exit(str(e))


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
    # Without "prog", argparse infers it from how the process was actually launched
    # (eg. `python3 -m mag.cli` via the "mag" wrapper) rather than from what the user
    # typed (eg. "mag issue ..."). Pinned to "mag" so usage messages match our docstring.
    parser = argparse.ArgumentParser(
        prog="mag", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
        help="Add and/or remove a scope on an existing token, without rotating its secret",
        description="Add a new scope to or remove an existing one from a token. --add-scope and"
        "--remove-scope both require a scope to be specified like in `mag issue --scope`."
        "Specify the token to edit by id (see `mag list`). Its secret is not affected.",
    )
    p_edit.add_argument("token_id")
    p_edit.add_argument("--add-scope", metavar="PREFIX:METHODS")
    p_edit.add_argument("--remove-scope", metavar="PREFIX:METHODS")
    p_edit.set_defaults(func=cmd_edit)
    
    p_revoke = subparsers.add_parser("revoke", help="Revoke a token by id")
    p_revoke.add_argument("token_id")
    p_revoke.set_defaults(func=cmd_revoke)
    
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit("Run this via: mag <issue|list|edit|revoke>  (see setup.sh)")
