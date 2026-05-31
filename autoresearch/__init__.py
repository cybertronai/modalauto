"""Import shim for source checkouts with arbitrary directory names.

The project modules live at the repository root (``backend/``, ``experiments/``,
``frontend/``).  Exposing the repository root as this package path lets
``autoresearch.*`` imports work even when the checkout directory is not named
``autoresearch``.
"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
__path__ = [str(_ROOT)]
