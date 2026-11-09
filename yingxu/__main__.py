"""`python -m yingxu` starts the GUI; pass `process` to run the CLI."""
from __future__ import annotations

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "process":
        from .cli import main
        raise SystemExit(main(sys.argv[1:]))
    from .gui import main
    raise SystemExit(main())
