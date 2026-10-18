"""Root conftest so 'python3 -m pytest' works without an install step."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
