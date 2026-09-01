import os
import sys

# The repo root, so `import mag` / `from mag.lib import ...` resolves - the
# same PYTHONPATH the mag wrapper and mag-proxy.service set for real runs.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
