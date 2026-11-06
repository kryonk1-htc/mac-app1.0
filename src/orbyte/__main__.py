"""允许 `python -m orbyte` 直接启动。"""

from __future__ import annotations

import sys

from .main import main

if __name__ == "__main__":
    sys.exit(main())
