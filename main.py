"""Entry point for ManimSimpleRender.

Thin launcher that delegates to the application bootstrap (app/bootstrap.py),
which creates QApplication, loads + validates config, and wires the view and
controller. See the app/ package for the implementation.
"""

import sys

from app.bootstrap import main

if __name__ == "__main__":
    sys.exit(main())
