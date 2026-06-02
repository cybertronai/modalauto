# Team Architecture

Autoresearch is one manager coordinating a small agent team around an experiment runner.

Start it with:

```bash
python bin/autoresearch launch --fresh
```

## Core Loop

```text
manager -> gather context -> propose hypotheses -> implement candidates -> verify results -> update frontier
```

The manager watches queue pressure and starts more agents when needed.

## State

Each experiment keeps generated state under:

```text
experiments/<name>/journal/
experiments/<name>/worktrees/
```

The journal stores agent status, research memory, hypotheses, submissions, verifications, messages, and run artifacts. Worktrees are per-agent scratch spaces.

## Roles

- `topline_manager`: owns the loop and scales the team.
- `creative_explorer`: proposes next hypotheses.
- `global_searcher`: proposes bigger search jumps.
- `implementor`: runs the experiment runner for a claimed hypothesis.
- `verifier`: checks submissions and records results.
- `researcher`: adds useful context to the journal before and during search.

## Scaling

- More queued hypotheses -> more implementors.
- More pending submissions -> more verifiers.
- Too few queued hypotheses -> more explorers/searchers.

The manager applies this automatically. Use `--no-apply-scale` only when debugging a dry manager step.

## Useful Commands

```bash
python bin/autoresearch launch --fresh
python bin/autoresearch status --experiment matmul
python bin/autoresearch clean --experiment matmul
python bin/autoresearch clean --experiment matmul --yes
```

## Experiment Contract

Each experiment declares a runner in `workflow.json`. Implementors call that runner, the runner writes artifacts under the journal, and the verifier records the result.
