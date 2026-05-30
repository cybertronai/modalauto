# vizdoom-basic

ViZDoom Basic scenario scoring task for modalauto.

The task uses the sibling ViZDoom checkout one directory above this repository and evaluates policies on `scenarios/basic.cfg`. Scoring is the mean `game.get_total_reward()` across configured seeds; higher is better.

## Run

```bash
/Users/seneca/Coding/ViZDoom/.venv/bin/python experiments/vizdoom-basic/loop.py --run-id smoke --episodes 3 --seeds 1,2,3
```

## Artifacts

Each run writes:

- `candidates.csv`: policy scores and per-seed rewards.
- `summary.json`: run metadata, top policies, and best score.
- `best_policy.json`: winning policy spec.
- `viz/<policy>.svg`: animated approach preview for each policy node.
- `journal/runs/<run-id>.md`: human-readable summary.
