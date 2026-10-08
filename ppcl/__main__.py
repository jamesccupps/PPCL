"""Entry point for ``python -m ppcl``.

Without this, ``python -m ppcl`` fails with "'ppcl' is a package and cannot be
directly executed", which is the shortest thing anyone types first.
"""

import sys

from .cli import main

sys.exit(main())
