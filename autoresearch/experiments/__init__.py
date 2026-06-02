"""Bridge package for top-level experiment folders.

Experiment folders stay at the repository root because they contain configs,
artifacts, and environment assets. This narrow package path keeps imports such
as ``autoresearch.experiments.matmul.loop`` working without exposing the whole
repository as the ``autoresearch`` package.
"""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[2] / "experiments")]
