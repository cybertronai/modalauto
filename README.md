# Autoresearch

Autoresearch runs agent teams against reproducible experiment folders. Each experiment owns its config, runner, journal, artifacts, and worktrees.

## Quick Start

From a fresh clone:

```bash
git clone <repo-url>
cd autoresearch

# Optional, but keeps local Python tools isolated.
python3 -m venv .venv
source .venv/bin/activate

# Installs TypeScript/Playwright tooling used for frontend checks.
npm --prefix frontend install

# Launch the default matmul experiment and dashboard.
python bin/autoresearch launch --fresh
```

Open the printed dashboard URL, usually:

```text
http://127.0.0.1:5176/
```

`--fresh` clears generated state before starting. Omit it to resume an existing journal.

The default `matmul` experiment uses only the Python standard library. Other experiments may have their own setup notes under `experiments/<name>/README.md`.

## Verify Setup

```bash
python -m py_compile autoresearch/backend/*.py frontend/scripts/*.py
npm --prefix frontend run check
python bin/autoresearch status --experiment matmul
```

## Run

Launch or resume the default experiment:

```bash
python bin/autoresearch launch
```

Start from a clean generated journal:

```bash
python bin/autoresearch launch --fresh
```

Limit a run while testing:

```bash
# Run one manager cycle, useful for checking scale decisions.
python bin/autoresearch launch --experiment matmul --fresh --max-steps 1

# Stop after 30 minutes.
python bin/autoresearch launch --experiment matmul --fresh --completion-max-seconds 1800

# Stop after 10 manager cycles without frontier improvement.
python bin/autoresearch launch --experiment matmul --fresh --completion-plateau-steps 10

# Stop when the experiment's workflow target is reached.
python bin/autoresearch launch --experiment matmul --fresh --completion-stop-on-target
```

## Common Commands

```bash
# Launch another experiment under experiments/
python bin/autoresearch launch --experiment openai_hide_and_seek

# Use an experiment outside this repo
python bin/autoresearch launch --experiment-root /path/to/experiment

# Show current team state
python bin/autoresearch status --experiment matmul

# Preview generated files that would be removed
python bin/autoresearch clean --experiment matmul

# Actually clear generated files and stop matching local agents
python bin/autoresearch clean --experiment matmul --yes

```

`bin/autoresearch` is the public entrypoint. Internal debugging forwards are documented in `docs/SUPPORT_COMMANDS.md`.

## Repository Layout

```text
autoresearch/ Python package
  backend/    orchestration, journal, launcher, agent runtime
bin/          single local CLI entrypoint
frontend/     browser dashboard and API scripts
experiments/  reproducible experiment folders
```

Generated state stays inside each experiment:

```text
experiments/<name>/
  workflow.json
  README.md
  journal/      # generated databases, messages, artifacts, run notes
  worktrees/    # generated agent workspaces
```

`journal/`, `worktrees/`, frontend builds, visualization builds, rollouts, model checkpoints, and local dependency folders are ignored by Git.

## Experiments

An experiment needs a `workflow.json` and a runner script. Minimal example:

```json
{
  "name": "my_env",
  "description": "Short description.",
  "domain": "custom",
  "direction": "minimize",
  "primary_metric": "score",
  "runner": {
    "command": "experiments/my_env/runner.py",
    "args": ["--experiment-root", "{experiment_root}"]
  },
  "paths": {
    "journal": "journal",
    "worktrees": "worktrees"
  }
}
```

`direction` tells agents and the dashboard which values are good. Use
`"minimize"` for cost/energy/loss metrics and `"maximize"` for reward/accuracy
metrics. Better values are rendered higher in the tree.

Supported placeholders in `runner.args`:

```text
{experiment_root}
{journal}
{worktrees}
{workflow}
```

## Runner Contract

Implementor agents call the runner with the configured args plus:

```text
--run-id <id>
--hypothesis-json <path>
--journal-root <path>
```

The runner should write artifacts under the journal and print a JSON summary to stdout:

```json
{
  "artifact_dir": "/abs/path/to/experiments/my_env/journal/artifacts/run-id",
  "best": {
    "name": "candidate-name",
    "score": 123,
    "semantic": "ok"
  }
}
```

Exit nonzero when a candidate cannot be built or evaluated.

## More Detail

See `TEAM_ARCHITECTURE.md` for the agent roles, scaling policy, message board, and journal design.
See `docs/SUPPORT_COMMANDS.md` for internal and compatibility commands.
