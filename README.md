# Autoresearch

Core orchestration code for running agentic research loops against reproducible experiment folders.

## Setup

Use Python 3.10+ from the parent directory of this package, or run the provided `bin/` entrypoints from this repository root. The entrypoints add the package parent to `PYTHONPATH` so imports like `autoresearch.backend...` resolve through the top-level `__init__.py`.

```bash
git clone <repo-url> autoresearch
cd autoresearch
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

The core autoresearch loop and the `matmul` reference experiment use only the Python standard library. No package install is required for the default run as long as commands are launched through `bin/`.

If you call modules directly instead of using `bin/`, expose the parent directory:

```bash
export PYTHONPATH="$(pwd)/..:${PYTHONPATH:-}"
python -m autoresearch.backend.team_journal --experiment matmul init
```

The frontend app is served by Python and loads React/Babel from the browser, so `npm install` is not required to view the live dashboard. Install Node dependencies only when running browser tests or Playwright-based checks:

```bash
cd frontend
npm install
cd ..
```

Experiment-specific environments may need their own dependencies. Keep those instructions inside `experiments/<name>/README.md` and keep generated virtualenvs, caches, journals, and worktrees out of Git.

## Layout

The repository root is split by responsibility:

- `backend/`: core Python orchestration loop.
- `frontend/`: the browser app and its API/support scripts.
- `bin/`: command-line entrypoints.
- `experiments/`: experiment-specific folders.

Each experiment owns its generated state:

```text
experiments/<experiment_name>/
  README.md
  workflow.json
  <environment source files>
  journal/
    team_journal.db
    research_memory.db
    messages/
    artifacts/
    runs/
  worktrees/
```

`journal/` and `worktrees/` are generated and ignored by Git. Track only the files needed to reproduce the experiment, usually `README.md`, `workflow.json`, source code, configs, and small fixtures.

## Full Multiagent Run

From this directory:

```bash
python bin/autoresearch-team --experiment matmul init
python bin/autoresearch-agent topline_manager --experiment matmul --agent-id manager-main --max-steps 100 --interval 5
python bin/autoresearch-team --experiment matmul status
```

Clear generated state for a fresh run. The command is a dry run unless `--yes`
is passed:

```bash
python bin/autoresearch-clear-runs --experiment matmul
python bin/autoresearch-clear-runs --experiment matmul --yes
```

This is the main autoresearch loop. The topline manager reads `experiments/matmul/workflow.json`, applies the scale plan, and spawns the rest of the team. All generated state stays inside the experiment folder:

- `experiments/matmul/journal/`: team DB, research memory, messages, run artifacts, and frontend changelog.
- `experiments/matmul/worktrees/`: agent-local workspaces and launcher logs.

The default experiment is `matmul`, so `--experiment matmul` can be omitted when running the reference experiment.

Use `--experiment <name>` for any experiment under `experiments/`, or `--experiment-root /path/to/env` for an external experiment folder.

## Frontend

Launch the live frontend against the active experiment journal:

```bash
cd frontend
FRONTEND_JOURNAL=../experiments/matmul/journal PORT=5176 python3 scripts/serve.py
```

Open `http://127.0.0.1:5176/`. The app streams updates from `/api/events`; no page reload is required while a multiagent run is writing to the journal.

## General-Purpose Experiment Setup

An experiment is any reproducible environment folder with a `workflow.json` runner config. The same manager/team loop works for every experiment; only the runner and environment source change.

Create an in-repo experiment:

```bash
mkdir -p experiments/my_env
touch experiments/my_env/README.md
```

Recommended folder shape:

```text
experiments/my_env/
  README.md
  workflow.json
  runner.py
  src/ or package files
  fixtures/              # optional small reproducible inputs
  journal/               # generated, ignored
  worktrees/             # generated, ignored
```

Add `experiments/my_env/workflow.json`. The manager loads this config, and implementor agents call the configured runner:

```json
{
  "name": "my_env",
  "description": "Short reproducible description of the environment.",
  "domain": "custom",
  "runner": {
    "command": "experiments/my_env/runner.py",
    "args": [
      "--experiment-root",
      "{experiment_root}"
    ]
  },
  "paths": {
    "journal": "journal",
    "worktrees": "worktrees"
  }
}
```

Supported `workflow.json` fields:

- `name`: stable experiment name.
- `description`: human-readable summary.
- `domain`: optional domain label for tooling/UI.
- `runner.command`: Python script or executable path. For in-repo experiments, use a path relative to this repository root, such as `experiments/my_env/runner.py`.
- `runner.args`: default args passed before each run's dynamic args. The placeholders `{experiment_root}`, `{journal}`, `{worktrees}`, and `{workflow}` are expanded from the selected experiment layout.
- `paths`: documented generated subfolders. The current layout helper uses `journal/` and `worktrees/` under the experiment root.

Then run the full manager/team loop against it:

```bash
python bin/autoresearch-team --experiment my_env init
python bin/autoresearch-agent topline_manager --experiment my_env --agent-id manager-main --max-steps 100 --interval 5
```

You can also keep an experiment outside this repo:

```bash
python bin/autoresearch-team --experiment-root /path/to/my_env init
python bin/autoresearch-agent topline_manager --experiment-root /path/to/my_env --agent-id manager-main --max-steps 100 --interval 5
```

`topline_manager` applies scale by default. It spawns explorers, searchers, researchers, implementors, verifiers, and other managers as needed. For a dry manager step that only prints/records intent without spawning workers, pass `--once --no-apply-scale`.

For self-spindown, combine `--allow-idle-retire` with a completion gate such as `--completion-plateau-steps N`, `--completion-max-seconds S`, or `--completion-stop-on-target`.

Environment variables are supported:

```bash
export AUTORESEARCH_EXPERIMENT=my_env
export AUTORESEARCH_EXPERIMENT_ROOT=/path/to/my_env
```

`AUTORESEARCH_EXPERIMENT_ROOT` takes precedence when both are set.

## Runner Contract

The implementor agents call the configured workflow runner with the `runner.args` from `workflow.json`, plus dynamic run arguments:

- `--run-id <id>`
- `--hypothesis-json <path>`
- `--journal-root <path>`

Your runner should:

- Read the hypothesis JSON if present and choose/construct a candidate from it.
- Write durable artifacts under the provided journal root.
- Print a JSON object to stdout with at least `artifact_dir` and a `best` summary.
- Exit nonzero if implementation failed, so the hypothesis can be abandoned/retried by the team.

Expected output shape:

```json
{
  "artifact_dir": "/abs/path/to/experiments/my_env/journal/artifacts/run-id",
  "best": {
    "name": "candidate-name",
    "family": "candidate-family",
    "score": 123,
    "semantic": "ok"
  },
  "elapsed_seconds": 1.23
}
```

Optional branch media can be returned in the same summary. Use this when an experiment can render a small timelapse GIF/video, rollout clip, plot animation, or other visual run preview for a candidate:

```json
{
  "media": {
    "timelapse": {
      "path": "timelapse.gif",
      "label": "candidate rollout"
    }
  }
}
```

`path` may be relative to the run artifact directory, relative to the experiment journal, or an absolute path inside the experiment journal. The frontend exposes it on the branch as `node.media.timelapse` and serves local files through `/api/media?node=<hypothesis_id>&kind=timelapse`.

The reference matmul runner lives at `experiments/matmul/loop.py` and writes:

- `journal/artifacts/<run-id>/summary.json`
- `journal/artifacts/<run-id>/best.ir`
- `journal/runs/<run-id>.md`

For a fully custom environment, keep the same reproducibility shape: the runner should consume an experiment root, write generated artifacts under that experiment's journal, and avoid absolute machine-specific paths in tracked files. The runner can call any local executable, simulator, benchmark, test suite, notebook export, or script as long as it returns the JSON summary above.

## Config Resolution

All commands accept either a named experiment or an explicit experiment root:

```bash
python bin/autoresearch-team --experiment my_env init
python bin/autoresearch-agent topline_manager --experiment my_env

python bin/autoresearch-team --experiment-root /path/to/my_env init
python bin/autoresearch-agent topline_manager --experiment-root /path/to/my_env
```

Resolution order:

1. `--experiment-root`, if provided.
2. `AUTORESEARCH_EXPERIMENT_ROOT`, if set.
3. `--experiment` or `AUTORESEARCH_EXPERIMENT`, resolved under `experiments/`.
4. Default experiment: `matmul`.

The resolved layout provides:

- `experiment_root`: selected experiment folder.
- `journal`: `<experiment_root>/journal`.
- `worktrees`: `<experiment_root>/worktrees`.
- `workflow`: `<experiment_root>/workflow.json`.

## Live Control

When the frontend server is running, the UI can write control actions into the active journal:

- Halt/resume a branch.
- Inject text as a prioritized child of a selected branch.
- Inject text as a new open branch.
- Select two branches and queue a gene-transfer hypothesis.

These controls are journal-backed and work during a live manager/team run. A halted branch prevents future generated hypotheses from using that branch ancestry as a parent; already queued or claimed work is not cancelled automatically.

## Generated Files

These are intentionally ignored:

- `experiments/*/journal/`
- `experiments/*/worktrees/`
- Python caches and build metadata
- frontend `node_modules/` and build outputs

If a result is important for reproduction, summarize it in the experiment README or commit a small fixture/config. Do not commit live databases, agent worktrees, raw logs, or large run artifacts.
