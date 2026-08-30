#!/usr/bin/env python3
"""mag - single command-line entry point for the mag project's scripts.

Usage:
    mag.py <command> [args...]

Commands:
    oauth    One-shot MYOB OAuth2 authorization        (oauth.py)
    issue    Issue a new API token                     (tokens.py)
    list     List API tokens and their scopes          (tokens.py)
    edit     Add a scope to an existing API token      (tokens.py)
    revoke   Revoke an API token                       (tokens.py)

Run `mag.py <command> --help` for command-specific options.
"""

import importlib
import sys

# oauth is a standalone script with no subcommands of its own.
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
    
    if command in SIMPLE_COMMANDS:
        module = importlib.import_module("oauth")
        module.main()
    elif command in TOKEN_COMMANDS:
        module = importlib.import_module("tokens")
        module.main()
    else:
        sys.exit(f"Unknown command {command!r} - run with --help to see available commands.")


if __name__ == "__main__":
    main()
