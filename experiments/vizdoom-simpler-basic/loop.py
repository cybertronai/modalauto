#!/usr/bin/env python3
"""ViZDoom SimplerBasic autoresearch runner.

This reuses the vizdoom-basic runner source with scenario/name substitutions so
it follows that task's conventions without depending on uncommitted edits there.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


_BASIC_LOOP = Path(__file__).resolve().parents[1] / "vizdoom-basic" / "loop.py"
_SOURCE = _BASIC_LOOP.read_text()
for _OLD, _NEW in {
    "vizdoom-basic": "vizdoom-simpler-basic",
    "vizdoom_basic_v1": "vizdoom_simpler_basic_v1",
    "basic.cfg": "simpler_basic.cfg",
    "Basic": "SimplerBasic",
}.items():
    _SOURCE = _SOURCE.replace(_OLD, _NEW)

_MODULE = types.ModuleType("vizdoom_simpler_basic_loop")
sys.modules[_MODULE.__name__] = _MODULE
_MODULE.__file__ = str(_BASIC_LOOP)
exec(compile(_SOURCE, str(_BASIC_LOOP), "exec"), _MODULE.__dict__)
main = _MODULE.main


if __name__ == "__main__":
    raise SystemExit(main())
