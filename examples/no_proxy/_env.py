"""Tiny, dependency-free ".env" loader shared by the no_proxy/ examples.

"no_proxy" examples access myob_client.py directly, which expects certain
environment variables. In a deployment, these variables will be defined in
a ".env" in the project root. Pull them in, just like the mag CLI wrapper
and systemd's EnvironmentFile= does.

Only sets a variable if it isn't already in the environment, so an explicit
override (e.g. `MYOB_CLIENT_ID=... python3 examples/no_proxy/...`) still wins.
"""

import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENV_FILE = os.path.join(ROOT_DIR, ".env")


def load() -> None:
    if not os.path.exists(ENV_FILE):
        return
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if key and key not in os.environ:
                os.environ[key] = value
