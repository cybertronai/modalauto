#!/usr/bin/env python3
"""ViZDoom Defend the Center autoresearch runner."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


os.environ.setdefault("VIZDOOM_EXPERIMENT_NAME", "vizdoom-defend-the-center")
os.environ.setdefault("VIZDOOM_SCENARIO_FILE", "defend_the_center.cfg")
os.environ.setdefault("VIZDOOM_SCENARIO_TITLE", "Defend the Center")
os.environ.setdefault("VIZDOOM_DEFAULT_RUN_ID", "vizdoom_defend_the_center_v1")

_BASIC_LOOP = Path(__file__).resolve().parents[1] / "vizdoom-basic" / "loop.py"
_SPEC = importlib.util.spec_from_file_location("vizdoom_basic_loop", _BASIC_LOOP)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"could not load shared ViZDoom loop from {_BASIC_LOOP}")

_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
main = _MODULE.main


if __name__ == "__main__":
    raise SystemExit(main())
