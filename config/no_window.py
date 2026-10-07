"""Shared Windows no-console-window subprocess flag.

Single source of truth for ``creationflags=`` on every ``subprocess`` spawn of
an external executable. Without it, a console-less parent (Streamlit under
the app-launcher, a scheduled task, a detached wrapper) flashes a new console
window on screen for every spawn. See the fleet-wide global ``CLAUDE.md``
("Subprocess spawns must suppress the console window (Windows)",
fleet-config#399) — repos with 3+ call sites use one helper instead of
re-deriving the ternary at each site.
"""

from __future__ import annotations

import subprocess
import sys

NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

__all__ = ["NO_WINDOW"]
