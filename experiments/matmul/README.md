# Matmul Reference Experiment

Minimal reference experiment for the autoresearch loop.

Run the full config-backed multiagent loop from the repository root:

```bash
python bin/autoresearch launch --experiment matmul --fresh
python bin/autoresearch status --experiment matmul
```

The manager reads this folder's `workflow.json` and spawns the agent team. Generated journals, artifacts, messages, and worktrees stay under this folder.

Layout:

- `loop.py`: matmul-specific autoresearch runner.
- `matmul/`: scorer and semantic verification helpers.
- `workflow.json`: workflow metadata and default runner.
- `journal/`: generated team journal, research memory, messages, run notes, and artifacts.
- `worktrees/`: generated agent-local workspaces and logs.

`journal/` and `worktrees/` are generated and ignored. The runner and workflow files are the tracked experiment surface.
