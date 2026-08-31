import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("app", "cli"):
    _path = os.path.join(ROOT_DIR, _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)
