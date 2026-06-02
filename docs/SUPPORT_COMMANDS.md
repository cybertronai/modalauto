# Support Commands

The public command is:

```bash
python bin/autoresearch <command>
```

Normal commands:

```bash
python bin/autoresearch launch --fresh
python bin/autoresearch status --experiment matmul
python bin/autoresearch clean --experiment matmul
```

`bin/autoresearch` also has hidden forwarding commands for debugging internals:

```bash
# Run one manager step without spawning workers. Useful for inspecting the scale plan.
python bin/autoresearch agent -- topline_manager --experiment matmul --once --no-apply-scale

# Print the manager's desired role counts and spawn/retire actions.
python bin/autoresearch team -- --experiment matmul scale-plan

# Query the experiment's research memory.
python bin/autoresearch memory -- --experiment matmul search "matrix multiplication"

# Read pending messages for a specific agent.
python bin/autoresearch board -- --experiment matmul inbox --agent-id manager-main

# Serve only the dashboard for an existing journal.
python bin/autoresearch frontend -- --journal experiments/matmul/journal --port 5176
```

These are support paths, not the normal interface.

Python package code lives under `autoresearch/`. The top-level `frontend/` and
`experiments/` folders stay at the repository root because they are runnable
app/experiment assets, not core package modules.
