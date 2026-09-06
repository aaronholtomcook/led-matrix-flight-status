"""
Detects whether to use the real rgbmatrix hardware bindings or the
rgbmatrix_sim emulator, and exposes RGBMatrix/RGBMatrixOptions/graphics from
whichever one is active, plus the correct LOG_FILE path for each.

Import this before anything else that needs `graphics` or the matrix
classes — the detection runs once, at import time.
"""

import argparse
import sys

# Detect --dev-mode early, independently of the main argparse setup in
# test.py, since we need to know the backend before most other imports.
# parse_known_args() here so an unrecognized --dev-mode doesn't blow up
# before the real parser (in test.py) sees it.
_early_parser = argparse.ArgumentParser(add_help=False)
_early_parser.add_argument("--dev-mode", action="store_true",
                            help="Run in development mode (use emulator).")
_early_args, _ = _early_parser.parse_known_args()

# Use the emulator whenever --dev-mode is explicitly passed, OR automatically
# on Windows (since the real rgbmatrix hardware bindings are Pi/Linux-only
# and won't import there at all).
USE_DEV_MODE = _early_args.dev_mode or sys.platform == "win32"

if USE_DEV_MODE:
    from rgbmatrix_sim import RGBMatrix, RGBMatrixOptions, graphics
    from dotenv import load_dotenv
    load_dotenv()
    LOG_FILE = "./dev.log"
else:
    from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
    # Log file lives in /tmp rather than under the home directory. RGBMatrix()
    # drops privileges from root to the 'daemon' user once initialized, and
    # 'daemon' often can't traverse into /home/<user>/... — /tmp is always
    # writable by everyone, so logging keeps working after that privilege drop.
    LOG_FILE = "/tmp/flight_status_matrix.log"
