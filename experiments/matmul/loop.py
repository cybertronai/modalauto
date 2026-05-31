#!/usr/bin/env python3
"""Simplified matmul autoresearch loop.

This is a compact copy of the journal loop from sutro-problems-journal. It is
single-process on purpose: generate a bounded candidate batch, score it with the
real scorer, verify the best candidate semantically, and write journal-shaped
artifacts that a later multiagent loop can hand off between roles.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from autoresearch.backend import experiment_config
from autoresearch.experiments.matmul.matmul import matmul


N = 16
REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNAL_ROOT = experiment_config.DEFAULT_JOURNAL_DIR


@dataclass
class Candidate:
    name: str
    ir: str
    family: str
    notes: str = ""


def cost(addr: int) -> int:
    return math.isqrt(addr - 1) + 1


def verify_general(ir: str, cases: int, seed: int) -> tuple[bool, str]:
    rng = random.Random(seed)
    for case in range(cases):
        inputs = [rng.randint(-7, 7) for _ in range(2 * N * N)]
        a = [inputs[i * N:(i + 1) * N] for i in range(N)]
        off = N * N
        b = [inputs[off + i * N:off + (i + 1) * N] for i in range(N)]
        expected = [
            sum(a[i][k] * b[k][j] for k in range(N))
            for i in range(N)
            for j in range(N)
        ]
        try:
            actual, _ = matmul._simulate(ir, inputs)
        except Exception as exc:  # noqa: BLE001
            return False, f"simulate_error_case_{case}: {exc}"
        if actual != expected:
            return False, f"wrong_output_case_{case}"
    return True, "ok"


def buckets(ir: str) -> dict[str, int]:
    _, ops, outs = matmul._parse(ir)
    out = Counter()
    for op, operands in ops:
        if op == "copy":
            out["copy_reads"] += cost(operands[1])
            continue
        if len(operands) == 3:
            _, src1, src2 = operands
        else:
            dest, src2 = operands
            src1 = dest
        read_cost = cost(src1) + cost(src2)
        out[f"{op}_reads"] += read_cost
    out["output_reads"] = sum(cost(addr) for addr in outs)
    out["ops"] = len(ops)
    return dict(out)


def make_baseline() -> Candidate:
    return Candidate(
        name="baseline_16x16",
        ir=matmul.generate_baseline_16x16(),
        family="baseline",
        notes="naive triple loop",
    )


def make_tiled(tile: int) -> Candidate:
    if tile == 4:
        ir = matmul.generate_tiled_16x16()
        return Candidate("tiled_4x4", ir, "tiled", "copied baseline tiled generator")

    n = N
    scratch_size = 3 * tile * tile + 1
    scratch_base = 1
    input_base = scratch_base + scratch_size
    c_base = input_base + 2 * n * n
    s_a = lambda ii, kk: scratch_base + ii * tile + kk
    s_b = lambda kk, jj: scratch_base + tile * tile + kk * tile + jj
    s_c = lambda ii, jj: scratch_base + 2 * tile * tile + ii * tile + jj
    tmp = scratch_base + 3 * tile * tile
    a_at = lambda i, k: input_base + i * n + k
    b_at = lambda k, j: input_base + n * n + k * n + j
    c_at = lambda i, j: c_base + i * n + j

    inputs = [a_at(i, k) for i in range(n) for k in range(n)]
    inputs += [b_at(k, j) for k in range(n) for j in range(n)]
    outputs = [c_at(i, j) for i in range(n) for j in range(n)]
    lines = [",".join(map(str, inputs))]

    for i0 in range(0, n, tile):
        for j0 in range(0, n, tile):
            for k0 in range(0, n, tile):
                for ii in range(tile):
                    for kk in range(tile):
                        lines.append(f"copy {s_a(ii, kk)},{a_at(i0 + ii, k0 + kk)}")
                for kk in range(tile):
                    for jj in range(tile):
                        lines.append(f"copy {s_b(kk, jj)},{b_at(k0 + kk, j0 + jj)}")
                for ii in range(tile):
                    for jj in range(tile):
                        for kk in range(tile):
                            lines.append(f"mul {tmp},{s_a(ii, kk)},{s_b(kk, jj)}")
                            if k0 == 0 and kk == 0:
                                lines.append(f"copy {s_c(ii, jj)},{tmp}")
                            else:
                                lines.append(f"add {s_c(ii, jj)},{tmp}")
            for ii in range(tile):
                for jj in range(tile):
                    lines.append(f"copy {c_at(i0 + ii, j0 + jj)},{s_c(ii, jj)}")

    lines.append(",".join(map(str, outputs)))
    return Candidate(f"tiled_{tile}x{tile}", "\n".join(lines), "tiled")


def make_panel(ti: int, tj: int, tk: int, cache_a: bool, cache_b: bool) -> Candidate:
    n = N
    c_size = ti * tj
    a_size = ti * tk if cache_a else 1
    b_size = tk * tj if cache_b else 1
    c_base = 1
    a_base = c_base + c_size
    b_base = a_base + a_size
    tmp = b_base + b_size
    input_base = tmp + 1
    output_base = input_base + 2 * n * n

    a_in = lambda i, k: input_base + i * n + k
    b_in = lambda k, j: input_base + n * n + k * n + j
    c_slot = lambda ii, jj: c_base + ii * tj + jj
    a_slot = lambda ii, kk: a_base + (ii * tk + kk if cache_a else 0)
    b_slot = lambda kk, jj: b_base + (kk * tj + jj if cache_b else 0)
    out = lambda i, j: output_base + i * n + j

    inputs = [a_in(i, k) for i in range(n) for k in range(n)]
    inputs += [b_in(k, j) for k in range(n) for j in range(n)]
    outputs = [out(i, j) for i in range(n) for j in range(n)]
    lines = [",".join(map(str, inputs))]

    for i0 in range(0, n, ti):
        for j0 in range(0, n, tj):
            for k0 in range(0, n, tk):
                if cache_a:
                    for ii in range(ti):
                        for kk in range(tk):
                            lines.append(f"copy {a_slot(ii, kk)},{a_in(i0 + ii, k0 + kk)}")
                if cache_b:
                    for kk in range(tk):
                        for jj in range(tj):
                            lines.append(f"copy {b_slot(kk, jj)},{b_in(k0 + kk, j0 + jj)}")
                for ii in range(ti):
                    for jj in range(tj):
                        for kk in range(tk):
                            if not cache_a:
                                lines.append(f"copy {a_slot(ii, kk)},{a_in(i0 + ii, k0 + kk)}")
                            if not cache_b:
                                lines.append(f"copy {b_slot(kk, jj)},{b_in(k0 + kk, j0 + jj)}")
                            if k0 == 0 and kk == 0:
                                lines.append(f"mul {c_slot(ii, jj)},{a_slot(ii, kk)},{b_slot(kk, jj)}")
                            else:
                                lines.append(f"mul {tmp},{a_slot(ii, kk)},{b_slot(kk, jj)}")
                                lines.append(f"add {c_slot(ii, jj)},{tmp}")
            for ii in range(ti):
                for jj in range(tj):
                    lines.append(f"copy {out(i0 + ii, j0 + jj)},{c_slot(ii, jj)}")

    lines.append(",".join(map(str, outputs)))
    return Candidate(
        f"panel_{ti}x{tj}x{tk}_a{int(cache_a)}b{int(cache_b)}",
        "\n".join(lines),
        "panel",
    )


def make_panel_alloc(ti: int, tj: int, tk: int, cache_a: bool, cache_b: bool) -> Candidate:
    n = N
    c_slots = [f"C_{ii}_{jj}" for ii in range(ti) for jj in range(tj)]
    a_slots = [f"A_{ii}_{kk}" for ii in range(ti) for kk in range(tk)] if cache_a else ["A_0"]
    b_slots = [f"B_{kk}_{jj}" for kk in range(tk) for jj in range(tj)] if cache_b else ["B_0"]
    slots = c_slots + a_slots + b_slots + ["TMP"]
    ops: list[tuple[str, str, tuple[str, ...]]] = []
    input_reads = Counter()

    a_in = lambda i, k: f"in_A_{i}_{k}"
    b_in = lambda k, j: f"in_B_{k}_{j}"
    out = lambda i, j: f"out_{i}_{j}"

    for i0 in range(0, n, ti):
        for j0 in range(0, n, tj):
            for k0 in range(0, n, tk):
                if cache_a:
                    for ii in range(ti):
                        for kk in range(tk):
                            src = a_in(i0 + ii, k0 + kk)
                            ops.append(("copy", f"A_{ii}_{kk}", (src,)))
                            input_reads[src] += 1
                if cache_b:
                    for kk in range(tk):
                        for jj in range(tj):
                            src = b_in(k0 + kk, j0 + jj)
                            ops.append(("copy", f"B_{kk}_{jj}", (src,)))
                            input_reads[src] += 1
                for ii in range(ti):
                    for jj in range(tj):
                        for kk in range(tk):
                            aa = f"A_{ii}_{kk}" if cache_a else "A_0"
                            bb = f"B_{kk}_{jj}" if cache_b else "B_0"
                            if not cache_a:
                                src = a_in(i0 + ii, k0 + kk)
                                ops.append(("copy", aa, (src,)))
                                input_reads[src] += 1
                            if not cache_b:
                                src = b_in(k0 + kk, j0 + jj)
                                ops.append(("copy", bb, (src,)))
                                input_reads[src] += 1
                            if k0 == 0 and kk == 0:
                                ops.append(("mul", f"C_{ii}_{jj}", (aa, bb)))
                            else:
                                ops.append(("mul", "TMP", (aa, bb)))
                                ops.append(("add", f"C_{ii}_{jj}", (f"C_{ii}_{jj}", "TMP")))
            for ii in range(ti):
                for jj in range(tj):
                    ops.append(("copy", out(i0 + ii, j0 + jj), (f"C_{ii}_{jj}",)))

    slot_reads = Counter()
    for _, _, srcs in ops:
        for src in srcs:
            if src in slots:
                slot_reads[src] += 1

    addr: dict[str, int] = {}
    next_addr = 1
    for slot in sorted(slots, key=lambda s: (-slot_reads[s], s)):
        addr[slot] = next_addr
        next_addr += 1

    input_names = [a_in(i, k) for i in range(n) for k in range(n)]
    input_names += [b_in(k, j) for k in range(n) for j in range(n)]
    for offset, name in enumerate(sorted(input_names, key=lambda s: (-input_reads[s], s))):
        addr[name] = next_addr + offset

    output_base = next_addr + 2 * n * n
    for i in range(n):
        for j in range(n):
            addr[out(i, j)] = output_base + i * n + j

    lines = [",".join(str(addr[name]) for name in input_names)]
    for op, dst, srcs in ops:
        if op == "copy":
            lines.append(f"copy {addr[dst]},{addr[srcs[0]]}")
        else:
            lines.append(f"{op} {addr[dst]},{addr[srcs[0]]},{addr[srcs[1]]}")
    lines.append(",".join(str(addr[out(i, j)]) for i in range(n) for j in range(n)))

    hot = ",".join(f"{slot}:{slot_reads[slot]}" for slot in sorted(slots, key=lambda s: (-slot_reads[s], s))[:6])
    return Candidate(
        f"panelalloc_{ti}x{tj}x{tk}_a{int(cache_a)}b{int(cache_b)}",
        "\n".join(lines),
        "trace_alloc",
        f"hot_slots={hot}",
    )


def make_sa_cache(ti: int = 8, tj: int = 4) -> Candidate:
    n = N
    if n % ti or n % tj:
        raise ValueError(f"tile shape must divide 16: {ti}x{tj}")
    sa = 1
    tmp = 2
    sb = lambda jj: 3 + jj
    sc_base = 3 + tj
    sc = lambda ii, jj: sc_base + ii * tj + jj
    a_base = sc_base + ti * tj
    b_base = a_base + n * n
    c_base = b_base + n * n
    a = lambda i, k: a_base + i * n + k
    b = lambda k, j: b_base + k * n + j
    c = lambda i, j: c_base + i * n + j

    inputs = [a(i, k) for i in range(n) for k in range(n)]
    inputs += [b(k, j) for k in range(n) for j in range(n)]
    outputs = [c(i, j) for i in range(n) for j in range(n)]
    lines = [",".join(map(str, inputs))]
    for bi in range(0, n, ti):
        for bj in range(0, n, tj):
            for k in range(n):
                for jj in range(tj):
                    lines.append(f"copy {sb(jj)},{b(k, bj + jj)}")
                for ii in range(ti):
                    lines.append(f"copy {sa},{a(bi + ii, k)}")
                    for jj in range(tj):
                        if k == 0:
                            lines.append(f"mul {sc(ii, jj)},{sa},{sb(jj)}")
                        else:
                            lines.append(f"mul {tmp},{sa},{sb(jj)}")
                            lines.append(f"add {sc(ii, jj)},{tmp}")
            for ii in range(ti):
                for jj in range(tj):
                    lines.append(f"copy {c(bi + ii, bj + jj)},{sc(ii, jj)}")
    lines.append(",".join(map(str, outputs)))
    return Candidate(f"sa_cache_{ti}x{tj}", "\n".join(lines), "sa_cache")


def make_scratch_cache_search(ti: int, tj: int) -> Candidate:
    cand = make_sa_cache(ti, tj)
    return Candidate(
        f"scratch_hotread_{ti}x{tj}",
        cand.ir,
        "scratch_layout_search",
        "generic hot-read low-address cache schedule",
    )


def make_dead_input_outputs_packed() -> Candidate:
    n = N
    tio, tjo = 4, 8
    tii, tji = 4, 1
    n_ibo, n_jbo = n // tio, n // tjo
    n_jbi = tjo // tji

    sb = 1
    tmp = 2
    sa = lambda ii: 3 + ii
    sc = lambda jb_in, ii: 7 + jb_in * tii + ii
    b_base = 39
    a_base = b_base + n * n
    c_base = a_base + n * n

    b_hot = [(k, j) for k in range(4) for j in range(8)]
    hot = set(b_hot)
    b_rest = [(k, j) for k in range(n) for j in range(n) if (k, j) not in hot]
    b_addr = {kj: b_base + idx for idx, kj in enumerate(b_hot + b_rest)}
    a = lambda i, k: a_base + i * n + k
    b = lambda k, j: b_addr[(k, j)]

    last_bi_o, last_bj_o = n_ibo - 1, n_jbo - 1
    dead: list[int] = []
    used: set[int] = set()
    c_spill = [c_base + x for x in range(tio * tjo)]
    c_spill_pos = 0
    out_addr: dict[tuple[int, int], int] = {}

    for bi_o in range(n_ibo):
        for bj_o in range(n_jbo):
            block_cells = [
                (bi_o * tio + ii, bj_o * tjo + jb_in)
                for jb_in in range(n_jbi)
                for ii in range(tii)
            ]
            if (bi_o, bj_o) == (last_bi_o, last_bj_o):
                for i, j in block_cells:
                    out_addr[(i, j)] = sc(j - last_bj_o * tjo, i - last_bi_o * tio)
                continue
            if bj_o == n_jbo - 1:
                for ii in range(tio):
                    i = bi_o * tio + ii
                    for k in range(n):
                        dead.append(a(i, k))
            if bi_o == n_ibo - 1:
                for k in range(n):
                    for jb_in in range(n_jbi):
                        j = bj_o * tjo + jb_in
                        dead.append(b(k, j))
            dead.sort()
            for i, j in block_cells:
                while dead and dead[0] in used:
                    dead.pop(0)
                if dead:
                    addr = dead.pop(0)
                else:
                    addr = c_spill[c_spill_pos]
                    c_spill_pos += 1
                used.add(addr)
                out_addr[(i, j)] = addr

    inputs = [a(i, k) for i in range(n) for k in range(n)]
    inputs += [b(k, j) for k in range(n) for j in range(n)]
    outputs = [out_addr[(i, j)] for i in range(n) for j in range(n)]
    lines = [",".join(map(str, inputs))]
    for bi_o in range(n_ibo):
        for bj_o in range(n_jbo):
            for k in range(n):
                for ii in range(tii):
                    lines.append(f"copy {sa(ii)},{a(bi_o * tio + ii, k)}")
                for jb_in in range(n_jbi):
                    j = bj_o * tjo + jb_in
                    lines.append(f"copy {sb},{b(k, j)}")
                    for ii in range(tii):
                        if k == 0:
                            lines.append(f"mul {sc(jb_in, ii)},{sa(ii)},{sb}")
                        elif ii < tii - 1:
                            lines.append(f"mul {tmp},{sa(ii)},{sb}")
                            lines.append(f"add {sc(jb_in, ii)},{tmp}")
                        else:
                            lines.append(f"mul {sb},{sa(ii)},{sb}")
                            lines.append(f"add {sc(jb_in, ii)},{sb}")
            if (bi_o, bj_o) == (last_bi_o, last_bj_o):
                continue
            for jb_in in range(n_jbi):
                j = bj_o * tjo + jb_in
                for ii in range(tii):
                    i = bi_o * tio + ii
                    lines.append(f"copy {out_addr[(i, j)]},{sc(jb_in, ii)}")
    lines.append(",".join(map(str, outputs)))
    return Candidate("dead_input_outputs_packed", "\n".join(lines), "dead_input_output_reuse")


def strategy_from_hypothesis(path: Path | None, fallback: str, disable_meta_operator: bool = False) -> str:
    if path is None:
        return fallback
    if disable_meta_operator:
        return fallback
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return fallback
    operator = payload.get("operator")
    if operator == "enumerate_panels":
        return "baseline"
    if operator == "enumerate_schedule_family":
        return "hypothesis_schedule"
    if operator == "schedule_from_reasoning":
        return "hypothesis_schedule"
    return fallback


def candidate_batch(include_reference_mechanisms: bool = False, strategy: str = "baseline") -> list[Candidate]:
    candidates = [make_baseline(), make_tiled(2), make_tiled(4), make_tiled(8)]
    if strategy in {"sa_cache", "global_reorient", "all"}:
        candidates.extend([make_sa_cache(8, 4), make_sa_cache(4, 8), make_sa_cache(4, 4)])
    if strategy in {"dead_io", "global_reorient", "all"}:
        candidates.append(make_dead_input_outputs_packed())
    if strategy in {"layout_search", "scratch_search", "alias_search", "hypothesis_schedule"}:
        for ti, tj in [(2, 8), (4, 4), (4, 8), (8, 2), (8, 4)]:
            if N % ti == 0 and N % tj == 0:
                candidates.append(make_scratch_cache_search(ti, tj))
    for ti, tj, tk in [(2, 4, 1), (4, 2, 1), (4, 4, 1), (8, 2, 1)]:
        for cache_a, cache_b in [(True, True), (True, False), (False, True)]:
            candidates.append(make_panel(ti, tj, tk, cache_a, cache_b))
            if include_reference_mechanisms:
                candidates.append(make_panel_alloc(ti, tj, tk, cache_a, cache_b))
    return candidates


def score_candidates(
    candidates: list[Candidate],
    verify_cases: int,
    seed: int,
    verify_top: int,
    avoid_candidates: set[str] | None = None,
) -> list[dict[str, object]]:
    avoid_candidates = avoid_candidates or set()
    rows = []
    for cand in candidates:
        row: dict[str, object] = {
            "name": cand.name,
            "family": cand.family,
            "notes": cand.notes,
            "score": "",
            "semantic": "not_run",
            "error": "",
        }
        try:
            score = matmul.score_16x16(cand.ir)
            row["score"] = score
            row.update(buckets(cand.ir))
        except Exception as exc:  # noqa: BLE001
            row["semantic"] = "invalid"
            row["error"] = str(exc)
        rows.append(row)
    rows.sort(key=lambda r: int(r["score"]) if r["score"] != "" else 10**12)

    by_name = {cand.name: cand for cand in candidates}
    checked = 0
    for row in rows:
        if row["score"] == "" or row["semantic"] == "invalid":
            continue
        if str(row["name"]) in avoid_candidates:
            row["semantic"] = "duplicate"
            continue
        if checked >= verify_top:
            row["semantic"] = "unchecked"
            continue
        ok, message = verify_general(by_name[str(row["name"])].ir, verify_cases, seed)
        row["semantic"] = "ok" if ok else "invalid"
        row["error"] = "" if ok else message
        checked += 1
    return rows


def write_run(
    run_id: str,
    rows: list[dict[str, object]],
    candidates: list[Candidate],
    target: int,
    include_reference_mechanisms: bool,
    strategy: str,
    journal_root: Path,
    avoid_candidates: set[str] | None = None,
) -> Path:
    artifact_dir = journal_root / "artifacts" / run_id
    run_dir = journal_root / "runs"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    fields = sorted({key for row in rows for key in row})
    with (artifact_dir / "candidate_scores.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    avoid_candidates = avoid_candidates or set()
    best_row = next(
        (
            row for row in rows
            if row["semantic"] == "ok"
            and row["score"] != ""
            and str(row["name"]) not in avoid_candidates
        ),
        None,
    )
    if best_row is None:
        best_row = next((row for row in rows if row["semantic"] == "ok" and row["score"] != ""), None)
    if best_row is None:
        raise RuntimeError("no valid candidate found")

    by_name = {cand.name: cand for cand in candidates}
    best = by_name[str(best_row["name"])]
    best_path = artifact_dir / "best.ir"
    best_path.write_text(best.ir + "\n")

    summary = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "blind": not include_reference_mechanisms,
        "uses_reference_mechanisms": include_reference_mechanisms,
        "strategy": strategy,
        "target": target,
        "target_status": "target_met" if int(best_row["score"]) <= target else "continuing",
        "best": best_row,
        "avoided_candidates": sorted(avoid_candidates),
        "candidate_count": len(rows),
        "families": sorted({str(row["family"]) for row in rows}),
        "next_handoff": {
            "target_gap": max(0, int(best_row["score"]) - target),
            "failed_total_cost_movement": "output-low and multiply-count-only proxies were misleading in the source journal",
            "untested_cost_movement": "dead input/output storage reuse after each k-panel",
            "allowed_regressions": "allow output-read regressions when copy/mul/add reads fall more",
            "next_role": "local_optimizer",
        },
    }
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")

    note = run_dir / f"{run_id}.md"
    note.write_text(
        "\n".join(
            [
                f"# {run_id}",
                "",
                "Simplified single-process matmul loop.",
                "",
                f"- Blind run: `{str(not include_reference_mechanisms).lower()}`",
                f"- Uses reference mechanisms: `{str(include_reference_mechanisms).lower()}`",
                "",
                "## Result",
                "",
                f"- Best score: `{best_row['score']}`",
                f"- Best IR: `{best_path}`",
                f"- Source candidate: `{best_row['name']}`",
                f"- Semantic verification: `{best_row['semantic']}`",
                f"- Target status: `{summary['target_status']}`",
                "",
                "## Loop Record",
                "",
                "- Generated baseline, tiled, and rectangular panel families.",
                "- Scored every candidate with the real 16x16 scorer.",
                "- Verified scored candidates on random semantic cases.",
                "- Wrote CSV, summary JSON, best IR, and this run note.",
                "",
                "## Handoff",
                "",
                json.dumps(summary["next_handoff"], indent=2, sort_keys=True),
                "",
            ]
        )
    )
    return artifact_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", help="experiment name under experiments/")
    parser.add_argument("--experiment-root", type=Path, help="experiment directory containing journal/ and worktrees/")
    parser.add_argument("--run-id", default="simplified_loop_v1")
    parser.add_argument("--target", type=int, default=66_707)
    parser.add_argument("--verify-cases", type=int, default=8)
    parser.add_argument("--verify-top", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260521)
    parser.add_argument("--hypothesis-json", type=Path)
    parser.add_argument("--journal-root", type=Path)
    parser.add_argument("--avoid-candidates-json", default="[]")
    parser.add_argument("--disable-meta-operator", action="store_true")
    parser.add_argument(
        "--strategy",
        choices=[
            "baseline",
            "layout_search",
            "scratch_search",
            "alias_search",
            "sa_cache",
            "dead_io",
            "global_reorient",
            "all",
        ],
        default="baseline",
    )
    parser.add_argument(
        "--include-reference-mechanisms",
        action="store_true",
        help="include copied/reference mechanisms; invalid for blind from-scratch runs",
    )
    args = parser.parse_args(argv)
    exp = experiment_config.layout(args.experiment, args.experiment_root)
    args.experiment_root = exp.root
    args.journal_root = (args.journal_root or exp.journal_dir).expanduser().resolve()
    args.strategy = strategy_from_hypothesis(args.hypothesis_json, args.strategy, args.disable_meta_operator)
    try:
        avoid_candidates = {str(x) for x in json.loads(args.avoid_candidates_json)}
    except json.JSONDecodeError:
        avoid_candidates = set()

    start = time.perf_counter()
    candidates = candidate_batch(args.include_reference_mechanisms, args.strategy)
    rows = score_candidates(candidates, args.verify_cases, args.seed, args.verify_top, avoid_candidates)
    artifact_dir = write_run(
        args.run_id,
        rows,
        candidates,
        args.target,
        args.include_reference_mechanisms,
        args.strategy,
        args.journal_root.expanduser().resolve(),
        avoid_candidates,
    )
    best = next(row for row in rows if row["semantic"] == "ok" and row["score"] != "")
    print(json.dumps({
        "artifact_dir": str(artifact_dir),
        "best": best,
        "elapsed_seconds": round(time.perf_counter() - start, 3),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
