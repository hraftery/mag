"""Common file path.

DATA_DIR holds tokens.json, mag_tokens.json and proxy_audit.log. .env stays
in the project root, next to .env.example. It's config written ahead of time,
not data the app generates. setup.sh creates var/ (with the same group
ownership as the rest of the project). mag-proxy.service's ReadWritePaths is
scoped to just it, not the whole project.

Note that tests/conftest.py and examples/*.py still compute their own
ROOT_DIR, because they need it to find mag in the first place.
"""

import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(ROOT_DIR, "var")
