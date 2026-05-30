# vizdoom-defend-the-center

ViZDoom Defend the Center scenario scoring task for modalauto.

The task uses the parent ViZDoom checkout and evaluates policies on `scenarios/defend_the_center.cfg`. Scoring is the mean `game.get_total_reward()` across configured seeds; higher is better.

## Run

```bash
/Users/seneca/Coding/ViZDoom/.venv/bin/python experiments/vizdoom-defend-the-center/loop.py --run-id smoke --episodes 3 --seeds 1,2,3
```

## Artifacts

Each run writes:

- `candidates.csv`: policy scores and per-seed rewards.
- `summary.json`: run metadata, top policies, and best score.
- `best_policy.json`: winning policy spec.
- `viz/<policy>.gif`: animated approach preview for each policy node.
- `journal/runs/<run-id>.md`: human-readable summary.
