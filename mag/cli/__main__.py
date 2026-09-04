#!/usr/bin/env python3
"""Entry point for the mag command line interface.

Can be run via `python3 -m mag.cli`, but intended to be used via a `mag`
wrapper (see setup.sh). Do not use "mag.py" as a filename because that
would shadow the root level package name.

Usage:
    mag <command> [args...]

Commands:
    status   Service state, logs, and recent activity  (status.py)
    oauth    One-shot MYOB OAuth2 authorisation        (oauth.py)
    issue    Issue a new API token                     (tokens.py)
    list     List API tokens and their scopes          (tokens.py)
    edit     Add a scope to an existing API token      (tokens.py)
    revoke   Revoke an API token                       (tokens.py)

Run `mag <command> --help` for command-specific options.
"""

import importlib
import sys

# status and oauth are standalone scripts with no subcommands of their own.
STATUS_COMMANDS = {"status"}
OAUTH_COMMANDS = {"oauth"}
# issue/list/edit/revoke are themselves subcommands of tokens.py.
TOKEN_COMMANDS = {"issue", "list", "edit", "revoke"}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    if command in ("-h", "--help"):
        print(__doc__)
        return

    if command in STATUS_COMMANDS:
        module = importlib.import_module("mag.cli.status")
        module.main()
    elif command in OAUTH_COMMANDS:
        module = importlib.import_module("mag.cli.oauth")
        module.main()
    elif command in TOKEN_COMMANDS:
        module = importlib.import_module("mag.cli.tokens")
        module.main()
    else:
        sys.exit(f"Unknown command {command!r} - run with --help to see available commands.")


if __name__ == "__main__":
    main()
