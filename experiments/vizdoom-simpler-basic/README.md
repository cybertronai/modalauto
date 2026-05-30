# vizdoom-simpler-basic

ViZDoom SimplerBasic scenario scoring task for modalauto.

The task uses the parent ViZDoom checkout and evaluates policies on `scenarios/simpler_basic.cfg`. Scoring is the mean `game.get_total_reward()` across configured seeds; higher is better.

## Run

```bash
/Users/seneca/Coding/ViZDoom/.venv/bin/python experiments/vizdoom-simpler-basic/loop.py --run-id smoke --episodes 3 --seeds 1,2,3
```

## Artifacts

Each run writes:

- `candidates.csv`: policy scores and per-seed rewards.
- `summary.json`: run metadata, top policies, and best score.
- `best_policy.json`: winning policy spec.
- `viz/<policy>.gif`: animated approach preview for each policy node.
- `journal/runs/<run-id>.md`: human-readable summary.
