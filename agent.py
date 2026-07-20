"""Thin CLI entry point — delegates to autosre.agent.main.

Usage:
    python agent.py db
    python agent.py disk --simulate
    python agent.py --list
"""

from __future__ import annotations

import sys

from autosre.agent import main

if __name__ == "__main__":
    sys.exit(main())
