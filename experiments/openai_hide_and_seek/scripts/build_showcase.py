#!/usr/bin/env python3
"""Build public showcase artifacts from saved hide-and-seek Autoresearch runs."""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "visualization" / "public"
SHOWCASE = PUBLIC / "showcase"
ARTIFACTS = ROOT / "journal" / "artifacts"
ROLLOUTS = PUBLIC / "rollouts"


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-")
    return value[:96] or "run"


def metric_summary(summary: dict) -> dict:
    best = summary.get("best") if isinstance(summary.get("best"), dict) else {}
    training = summary.get("training") if isinstance(summary.get("training"), dict) else {}
    return {
        "score": best.get("score"),
        "hider_seen_rate": best.get("hider_seen_rate", training.get("final_hider_seen_rate")),
        "caught_fraction": best.get("caught_fraction", training.get("final_caught_fraction")),
        "mean_hider_reward": best.get("mean_hider_reward", training.get("final_mean_hider_reward")),
        "hider_improvement": best.get("hider_improvement"),
        "worlds": training.get("worlds"),
        "updates": training.get("updates"),
        "horizon": training.get("horizon"),
        "lr": training.get("lr"),
        "hidden": training.get("hidden"),
        "entropy_coef": training.get("entropy_coef"),
        "prep_fraction": training.get("prep_fraction"),
    }


def world_to_px(x: float, y: float, scale: float = 44.0, size: int = 640) -> tuple[int, int]:
    return int(size / 2 + x * scale), int(size / 2 - y * scale)


def draw_agent(draw: ImageDraw.ImageDraw, agent: dict, size: int = 640) -> None:
    x, y = float(agent["pos"][0]), float(agent["pos"][1])
    px, py = world_to_px(x, y, size=size)
    yaw = float(agent.get("yaw") or 0.0)
    is_hider = agent.get("team") == "hider"
    color = (66, 190, 235) if is_hider else (242, 83, 64)
    sight = (66, 205, 235, 58) if is_hider else (245, 70, 60, 70)
    radius = 13
    fov_len = 82 if is_hider else 105
    fov_half = 0.58 if is_hider else 0.52
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    center_angle = -yaw - math.pi / 2
    start = math.degrees(center_angle - fov_half)
    end = math.degrees(center_angle + fov_half)
    od.pieslice([px - fov_len, py - fov_len, px + fov_len, py + fov_len], start, end, fill=sight)
    draw.bitmap((0, 0), overlay)
    draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=color, outline=(255, 255, 255), width=2)
    ex = math.sin(yaw)
    ey = math.cos(yaw)
    draw.ellipse([px + ex * 7 - 3, py - ey * 7 - 3, px + ex * 7 + 3, py - ey * 7 + 3], fill=(255, 255, 255))


def draw_rollout_gif(rollout: dict, out_path: Path, title: str, metrics: dict) -> None:
    frames = rollout.get("frames") if isinstance(rollout.get("frames"), list) else []
    if not frames:
        return
    size = 640
    images = []
    sample = max(1, len(frames) // 48)
    font = ImageFont.load_default()
    for frame in frames[::sample]:
        img = Image.new("RGB", (size, size), (219, 225, 229))
        draw = ImageDraw.Draw(img, "RGBA")
        for v in range(-6, 7):
            a = world_to_px(v, -6, size=size)
            b = world_to_px(v, 6, size=size)
            c = world_to_px(-6, v, size=size)
            d = world_to_px(6, v, size=size)
            draw.line([a, b], fill=(145, 154, 162, 72), width=1)
            draw.line([c, d], fill=(145, 154, 162, 72), width=1)
        draw.rectangle([50, 50, 590, 590], outline=(245, 248, 250), width=12)
        draw.rectangle([56, 56, 584, 584], outline=(105, 116, 125), width=2)
        for box in frame.get("boxes", []):
            x, y = float(box["pos"][0]), float(box["pos"][1])
            px, py = world_to_px(x, y, size=size)
            draw.rounded_rectangle([px - 18, py - 18, px + 18, py + 18], radius=4, fill=(238, 194, 39), outline=(178, 139, 16))
        for ramp in frame.get("ramps", []):
            x, y = float(ramp["pos"][0]), float(ramp["pos"][1])
            px, py = world_to_px(x, y, size=size)
            draw.polygon([(px - 23, py + 17), (px + 23, py + 17), (px + 23, py - 17)], fill=(214, 157, 0), outline=(142, 105, 0))
        for agent in frame.get("agents", []):
            draw_agent(draw, agent, size=size)
        step = frame.get("step", 0)
        phase = "Blue build" if frame.get("prep") else "Red seek"
        seen = metrics.get("hider_seen_rate")
        label = f"{title} | {phase} step {step}"
        draw.rectangle([0, 0, size, 44], fill=(246, 249, 251, 224))
        draw.text((10, 10), label[:95], fill=(34, 43, 50), font=font)
        if seen is not None:
            draw.text((10, 26), f"hider_seen_rate={seen} caught={metrics.get('caught_fraction')}", fill=(72, 82, 90), font=font)
        images.append(img)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(out_path, save_all=True, append_images=images[1:], duration=95, loop=0, optimize=True)


def collect_runs() -> tuple[list[dict], list[dict]]:
    all_runs = []
    showcase = []
    for summary_path in sorted(ARTIFACTS.glob("*/summary.json")):
        summary = load_json(summary_path)
        artifact_dir = summary_path.parent
        rollout_path = artifact_dir / "rollout.json"
        run_id = artifact_dir.name
        item = {
            "id": run_id,
            "kind": "autoresearch_artifact",
            "artifact_dir": str(artifact_dir),
            "summary_path": str(summary_path),
            "hypothesis": summary.get("hypothesis"),
            "best": summary.get("best"),
            "metrics": metric_summary(summary),
            "has_rollout": rollout_path.exists(),
        }
        all_runs.append(item)
        if rollout_path.exists():
            slug = slugify(run_id)
            public_rollout = SHOWCASE / f"{slug}.json"
            public_gif = SHOWCASE / f"{slug}.gif"
            shutil.copyfile(rollout_path, public_rollout)
            draw_rollout_gif(load_json(rollout_path), public_gif, slug, item["metrics"])
            item.update({
                "rollout_url": f"/showcase/{public_rollout.name}",
                "gif_url": f"/showcase/{public_gif.name}",
            })
            showcase.append(item)
    for summary_path in sorted(ROLLOUTS.glob("mjwarp_ppo_train*.json")):
        summary = load_json(summary_path)
        run_id = summary_path.stem
        item = {
            "id": run_id,
            "kind": "public_training_summary",
            "summary_path": str(summary_path),
            "best": None,
            "training": summary,
            "metrics": {
                "hider_seen_rate": summary.get("final_hider_seen_rate"),
                "caught_fraction": summary.get("final_caught_fraction"),
                "mean_hider_reward": summary.get("final_mean_hider_reward"),
                "worlds": summary.get("worlds"),
                "updates": summary.get("updates"),
                "horizon": summary.get("horizon"),
            },
            "has_rollout": False,
        }
        all_runs.append(item)
    return all_runs, showcase


def main() -> None:
    SHOWCASE.mkdir(parents=True, exist_ok=True)
    all_runs, showcase = collect_runs()
    all_runs.sort(key=lambda x: str(x.get("id")))
    showcase.sort(key=lambda x: (x.get("metrics", {}).get("score") is None, x.get("metrics", {}).get("score") or 10**12, x.get("id")))
    (SHOWCASE / "all_runs.json").write_text(json.dumps({"runs": all_runs}, indent=2))
    (SHOWCASE / "showcase_manifest.json").write_text(json.dumps({"runs": showcase}, indent=2))
    print(json.dumps({"all_runs": len(all_runs), "showcase_rollouts": len(showcase), "dir": str(SHOWCASE)}, indent=2))


if __name__ == "__main__":
    main()
