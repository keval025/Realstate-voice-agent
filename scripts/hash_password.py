"""Create a dashboard password hash for a client YAML file.

    python scripts/hash_password.py "their-password"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients import hash_password  # noqa: E402

if len(sys.argv) != 2:
    raise SystemExit('Usage: python scripts/hash_password.py "password"')
print(hash_password(sys.argv[1]))
