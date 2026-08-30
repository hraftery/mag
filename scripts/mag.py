#!/usr/bin/env python3
"""mag - single command-line entry point for the mag project's scripts.

Usage:
    python3 scripts/mag.py <command> [args...]

Commands:
    oauth    One-shot MYOB OAuth2 authorization        (oauth_callback.py)
    issue    Issue a new API token                     (tokens.py)
    list     List API tokens and their scopes           (tokens.py)
    edit     Add a scope to an existing API token        (tokens.py)
    revoke   Revoke an API token                        (tokens.py)

Run `python3 scripts/mag.py <command> --help` for command-specific options.
"""

import importlib
import sys

# oauth is a standalone script with no subcommands of its own - dispatch
# straight to its main(). issue/list/edit/revoke are themselves subcommands
# of tokens.py, so those pass the command name straight through instead.
SIMPLE_COMMANDS = {"oauth": "oauth_callback"}
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
        module = importlib.import_module(SIMPLE_COMMANDS[command])
        sys.argv = [f"mag.py {command}"] + sys.argv[2:]
        module.main()
    elif command in TOKEN_COMMANDS:
        module = importlib.import_module("tokens")
        sys.argv = ["mag.py"] + sys.argv[1:]  # tokens.py parses "issue"/"list"/etc itself
        module.main()
    else:
        sys.exit(f"Unknown command {command!r} - run with --help to see available commands.")


if __name__ == "__main__":
    main()
