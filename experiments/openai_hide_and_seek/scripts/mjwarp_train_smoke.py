#!/usr/bin/env python3
"""Batched MJWarp PPO trainer for OpenAI-style hide-and-seek self-play."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np


SELF_DIM = 8
LIDAR_DIM = 32
ENTITY_DIM = 12
ACTION_DIM = 5  # move x/y, rotate, grab, lock
TEAM = np.array([1.0, 1.0, -1.0, -1.0], dtype=np.float32)
TYPE_AGENT = np.array([0, 1, 0, 0], dtype=np.float32)
TYPE_BOX = np.array([0, 0, 1, 0], dtype=np.float32)
TYPE_RAMP = np.array([0, 0, 0, 1], dtype=np.float32)


def decode_array(record):
    data = base64.b64decode(record["data_b64"])
    return np.frombuffer(data, dtype=np.dtype(record["dtype"])).reshape(record["shape"]).copy()


def set_batched_field(field, values):
    import warp as wp

    host = np.asarray(values, dtype=np.float32)
    target_shape = tuple(field.shape)
    if target_shape == host.shape:
        batch = host
    elif len(target_shape) == 2 and target_shape[1:] == host.shape:
        batch = np.broadcast_to(host, target_shape).copy()
    else:
        raise ValueError(f"cannot copy host shape {host.shape} into Warp field {target_shape}")
    field.assign(wp.array(batch, dtype=field.dtype, device=field.device))


def assign_field(field, values):
    import warp as wp

    field.assign(wp.array(np.asarray(values, dtype=np.float32), dtype=field.dtype, device=field.device))


def qpos_slots_by_prefix(mjm, prefix):
    import mujoco

    slots = {}
    for joint_id in range(mjm.njnt):
        body_id = int(mjm.jnt_bodyid[joint_id])
        body_name = mujoco.mj_id2name(mjm, mujoco.mjtObj.mjOBJ_BODY, body_id) or ""
        if body_name.startswith(prefix):
            slots.setdefault(body_name.split(":", 1)[0], []).append(int(mjm.jnt_qposadr[joint_id]))
    return np.asarray([sorted(slots[name])[:4] for name in sorted(slots)], dtype=np.int64)


def static_occluders(mjm):
    import mujoco

    out = []
    for geom_id in range(mjm.ngeom):
        name = mujoco.mj_id2name(mjm, mujoco.mjtObj.mjOBJ_GEOM, geom_id) or ""
        if name.startswith("wall"):
            out.append((np.asarray(mjm.geom_pos[geom_id], dtype=np.float32)[:2].copy(),
                        np.asarray(mjm.geom_size[geom_id], dtype=np.float32)[:2].copy()))
    return out


def segment_intersects_aabb(p1, p2, center, half_size):
    direction = p2 - p1
    tmin = np.zeros((p1.shape[0],), dtype=np.float32)
    tmax = np.ones((p1.shape[0],), dtype=np.float32)
    for axis in range(2):
        low = center[axis] - half_size[axis]
        high = center[axis] + half_size[axis]
        denom = direction[:, axis]
        parallel = np.abs(denom) < 1e-6
        outside = parallel & ((p1[:, axis] < low) | (p1[:, axis] > high))
        inv = np.zeros_like(denom)
        np.divide(1.0, denom, out=inv, where=~parallel)
        t1 = (low - p1[:, axis]) * inv
        t2 = (high - p1[:, axis]) * inv
        tmin = np.maximum(tmin, np.where(parallel, tmin, np.minimum(t1, t2)))
        tmax = np.minimum(tmax, np.where(parallel, tmax, np.maximum(t1, t2)))
        tmax[outside] = -1.0
    return tmax >= np.maximum(tmin, 0.0)


def line_blocked(p1, p2, occluders):
    blocked = np.zeros((p1.shape[0],), dtype=bool)
    for center, half_size in occluders:
        blocked |= segment_intersects_aabb(p1, p2, center, half_size)
    return blocked


def ray_aabb_distance(origin, direction, center, half_size, max_range):
    tmin = np.zeros((origin.shape[0],), dtype=np.float32)
    tmax = np.full((origin.shape[0],), max_range, dtype=np.float32)
    for axis in range(2):
        low = center[axis] - half_size[axis]
        high = center[axis] + half_size[axis]
        denom = direction[:, axis]
        parallel = np.abs(denom) < 1e-6
        outside = parallel & ((origin[:, axis] < low) | (origin[:, axis] > high))
        inv = np.zeros_like(denom)
        np.divide(1.0, denom, out=inv, where=~parallel)
        t1 = (low - origin[:, axis]) * inv
        t2 = (high - origin[:, axis]) * inv
        tmin = np.maximum(tmin, np.where(parallel, tmin, np.minimum(t1, t2)))
        tmax = np.minimum(tmax, np.where(parallel, tmax, np.maximum(t1, t2)))
        tmax[outside] = -1.0
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin <= max_range)
    return np.where(hit, np.maximum(tmin, 0.0), max_range)


def ray_circle_distance(origin, direction, center, radius, max_range):
    rel = center - origin
    proj = (rel * direction).sum(axis=1)
    closest2 = (rel * rel).sum(axis=1) - proj * proj
    radius2 = radius * radius
    hit = (proj > 0.0) & (closest2 <= radius2)
    offset = np.sqrt(np.maximum(radius2 - closest2, 0.0))
    dist = proj - offset
    hit &= (dist >= 0.0) & (dist <= max_range)
    return np.where(hit, dist, max_range)


def build_lidar(agent_state, box_state, ramp_state, occluders, max_range=8.0):
    worlds = agent_state.shape[0]
    bins = np.linspace(-np.pi, np.pi, LIDAR_DIM, endpoint=False, dtype=np.float32)
    lidar = np.ones((worlds, 4, LIDAR_DIM), dtype=np.float32)
    for agent in range(4):
        origin = agent_state[:, agent, :2]
        yaw = agent_state[:, agent, 3]
        for i, offset in enumerate(bins):
            angle = yaw + offset
            direction = np.stack([np.cos(angle), np.sin(angle)], axis=1).astype(np.float32)
            nearest = np.full((worlds,), max_range, dtype=np.float32)
            for center, half_size in occluders:
                nearest = np.minimum(nearest, ray_aabb_distance(origin, direction, center, half_size, max_range))
            for other in range(4):
                if other != agent:
                    nearest = np.minimum(
                        nearest,
                        ray_circle_distance(origin, direction, agent_state[:, other, :2], 0.32, max_range),
                    )
            for box in range(box_state.shape[1]):
                nearest = np.minimum(
                    nearest,
                    ray_circle_distance(origin, direction, box_state[:, box, :2], 0.45, max_range),
                )
            for ramp in range(ramp_state.shape[1]):
                nearest = np.minimum(
                    nearest,
                    ray_circle_distance(origin, direction, ramp_state[:, ramp, :2], 0.55, max_range),
                )
            lidar[:, agent, i] = nearest / max_range
    return lidar


def agent_visibility(agent_state, occluders, cone_angle=3 / 8 * np.pi):
    agent_xy = agent_state[:, :, :2]
    yaw = agent_state[:, :, 3]
    visible = np.zeros((agent_xy.shape[0], 4, 4), dtype=bool)
    for src in range(4):
        forward = np.stack([np.cos(yaw[:, src]), np.sin(yaw[:, src])], axis=1)
        for dst in range(4):
            if src == dst:
                continue
            rel = agent_xy[:, dst] - agent_xy[:, src]
            dist = np.linalg.norm(rel, axis=1)
            rel_dir = rel / np.maximum(dist[:, None], 1e-6)
            in_cone = (rel_dir * forward).sum(axis=1) >= np.cos(cone_angle)
            visible[:, src, dst] = in_cone & (dist > 1e-4) & ~line_blocked(agent_xy[:, src], agent_xy[:, dst], occluders)
    return visible


def visibility_reward(agent_state, occluders):
    visible = agent_visibility(agent_state, occluders)
    seeker_hider = visible[:, 2:, :2]
    hiders_seen = seeker_hider.any(axis=1)
    seekers_seeing = seeker_hider.any(axis=2)
    hider_rew = np.where(hiders_seen, -1.0, 1.0)
    seeker_rew = np.where(seekers_seeing, 1.0, -1.0)
    return np.concatenate([hider_rew, seeker_rew], axis=1).astype(np.float32), hiders_seen


def entity_features(self_state, entity_state, entity_type, visible, team_value, object_size=0.0):
    rel = entity_state[:, :2] - self_state[:, :2]
    dist = np.linalg.norm(rel, axis=1, keepdims=True)
    return np.concatenate([
        rel,
        dist,
        entity_state[:, 2:4],
        np.full((entity_state.shape[0], 1), object_size, dtype=np.float32),
        np.full((entity_state.shape[0], 1), team_value, dtype=np.float32),
        np.asarray(visible, dtype=np.float32).reshape(-1, 1),
        np.broadcast_to(entity_type[None, :], (entity_state.shape[0], 4)),
    ], axis=1).astype(np.float32)


def build_obs(agent_state, box_state, ramp_state, phase, occluders):
    worlds = agent_state.shape[0]
    visible_agents = agent_visibility(agent_state, occluders)
    lidar = build_lidar(agent_state, box_state, ramp_state, occluders)
    self_obs = np.zeros((worlds, 4, SELF_DIM), dtype=np.float32)
    entities = []
    masks = []
    for agent in range(4):
        st = agent_state[:, agent]
        self_obs[:, agent] = np.concatenate([
            st[:, :2],
            np.sin(st[:, 3:4]),
            np.cos(st[:, 3:4]),
            np.full((worlds, 1), TEAM[agent], dtype=np.float32),
            np.full((worlds, 1), 1.0 if phase == "prep" else 0.0, dtype=np.float32),
            np.zeros((worlds, 2), dtype=np.float32),
        ], axis=1)
        ents = []
        ent_masks = []
        for other in range(4):
            if other == agent:
                continue
            ents.append(entity_features(st, agent_state[:, other], TYPE_AGENT, visible_agents[:, agent, other], TEAM[other]))
            ent_masks.append(visible_agents[:, agent, other])
        for box in range(box_state.shape[1]):
            ents.append(entity_features(st, box_state[:, box], TYPE_BOX, np.ones(worlds, dtype=bool), 0.0, 0.5))
            ent_masks.append(np.ones(worlds, dtype=bool))
        for ramp in range(ramp_state.shape[1]):
            ents.append(entity_features(st, ramp_state[:, ramp], TYPE_RAMP, np.ones(worlds, dtype=bool), 0.0, 0.7))
            ent_masks.append(np.ones(worlds, dtype=bool))
        entities.append(np.stack(ents, axis=1))
        masks.append(np.stack(ent_masks, axis=1))
    return self_obs, lidar, np.stack(entities, axis=1), np.stack(masks, axis=1).astype(np.float32)


def manipulate_objects(qpos, qvel, object_slots, agent_state, object_state, actions, locked, phase):
    worlds = object_state.shape[0]
    if object_slots.size == 0:
        return qpos, qvel, locked
    for agent in range(4):
        if phase == "prep" and agent >= 2:
            continue
        rel = object_state[:, :, :2] - agent_state[:, agent, None, :2]
        dist = np.linalg.norm(rel, axis=-1)
        nearest = dist.argmin(axis=1)
        close = dist[np.arange(worlds), nearest] < 0.82
        grab = actions[:, agent, 3] > 0.0
        lock = actions[:, agent, 4] > 0.4
        for w in range(worlds):
            obj = nearest[w]
            slots = object_slots[obj]
            if close[w] and lock[w]:
                locked[w, obj] = not locked[w, obj]
            if close[w] and grab[w] and not locked[w, obj]:
                push = np.clip(actions[w, agent, :2], -1.0, 1.0) * 0.045
                qpos[w, slots[:2]] += push
                qvel[w, slots[:2]] = push / 0.02
            if locked[w, obj]:
                qvel[w, slots[:2]] = 0.0
    return qpos, qvel, locked


def make_model(hidden):
    print("train: importing torch", flush=True)
    import torch
    print(f"train: torch {torch.__version__}, cuda_available={torch.cuda.is_available()}", flush=True)
    import torch.nn as nn

    class EntityActorCritic(nn.Module):
        def __init__(self):
            super().__init__()
            self.self_net = nn.Sequential(nn.Linear(SELF_DIM, hidden), nn.Tanh())
            self.lidar_net = nn.Sequential(
                nn.Conv1d(1, 8, kernel_size=5, padding=2, padding_mode="circular"),
                nn.Tanh(),
                nn.Conv1d(8, 8, kernel_size=3, padding=1, padding_mode="circular"),
                nn.Tanh(),
                nn.Flatten(),
                nn.Linear(8 * LIDAR_DIM, hidden),
                nn.Tanh(),
            )
            self.ent_net = nn.Sequential(nn.Linear(ENTITY_DIM, hidden), nn.Tanh())
            self.q = nn.Linear(hidden, hidden, bias=False)
            self.k = nn.Linear(hidden, hidden, bias=False)
            self.v = nn.Linear(hidden, hidden, bias=False)
            self.main = nn.Sequential(nn.Linear(hidden * 3, hidden), nn.Tanh())
            self.actor = nn.Linear(hidden, ACTION_DIM)
            self.critic = nn.Linear(hidden, 1)
            self.log_std = nn.Parameter(torch.full((ACTION_DIM,), -0.6))

        def forward(self, self_obs, lidar, entities, masks):
            sh = self.self_net(self_obs)
            lh = self.lidar_net(lidar.reshape(-1, 1, LIDAR_DIM)).reshape(*self_obs.shape[:-1], hidden)
            eh = self.ent_net(entities)
            q = self.q(sh).unsqueeze(-2)
            score = (q * self.k(eh)).sum(-1) / np.sqrt(hidden)
            score = score.masked_fill(masks <= 0, -1e9)
            attn = torch.softmax(score, dim=-1)
            ctx = (attn.unsqueeze(-1) * self.v(eh)).sum(dim=-2)
            main = self.main(torch.cat([sh, lh, ctx], dim=-1))
            mean = self.actor(main)
            value = self.critic(main).squeeze(-1)
            return mean, value

    return EntityActorCritic()


def to_torch(arr, device):
    import torch

    return torch.as_tensor(arr, dtype=torch.float32, device=device)


def collect_rollout(mjw, mjw_model, mjw_data, mjm, model, device, qpos0, qvel0, ctrl0, slots, horizon, prep_steps, occluders):
    import torch

    worlds = tuple(mjw_data.qpos.shape)[0]
    set_batched_field(mjw_data.qpos, qpos0)
    set_batched_field(mjw_data.qvel, qvel0)
    if mjm.nu:
        set_batched_field(mjw_data.ctrl, ctrl0)
    locked = np.zeros((worlds, slots["objects"].shape[0]), dtype=bool)
    buf = {k: [] for k in [
        "self", "lidar", "entities", "masks", "actions", "logp", "values", "rewards", "dones", "caught",
        "hider_seen_rate",
    ]}
    for step_idx in range(horizon):
        phase = "prep" if step_idx < prep_steps else "seek"
        qpos = mjw_data.qpos.numpy()
        qvel = mjw_data.qvel.numpy()
        agent_state = qpos[:, slots["agents"]].reshape(worlds, 4, 4).astype(np.float32)
        box_state = qpos[:, slots["boxes"]].reshape(worlds, slots["boxes"].shape[0], 4).astype(np.float32)
        ramp_state = qpos[:, slots["ramps"]].reshape(worlds, slots["ramps"].shape[0], 4).astype(np.float32)
        object_state = qpos[:, slots["objects"]].reshape(worlds, slots["objects"].shape[0], 4).astype(np.float32)
        self_obs, lidar, entities, masks = build_obs(agent_state, box_state, ramp_state, phase, occluders)
        with torch.no_grad():
            mean, value = model(to_torch(self_obs, device), to_torch(lidar, device), to_torch(entities, device), to_torch(masks, device))
            dist = torch.distributions.Normal(mean, model.log_std.exp())
            action = dist.sample()
            logp = dist.log_prob(action).sum(-1)
        actions_np = action.detach().cpu().numpy()
        if phase == "prep":
            actions_np[:, 2:, :] = 0.0
        qpos, qvel, locked = manipulate_objects(qpos, qvel, slots["objects"], agent_state, object_state, actions_np, locked, phase)
        assign_field(mjw_data.qpos, qpos)
        assign_field(mjw_data.qvel, qvel)
        ctrl = np.zeros((worlds, mjm.nu), dtype=np.float32)
        ctrl_view = ctrl[:, :12].reshape(worlds, 4, 3)
        ctrl_view[:, :, :2] = np.tanh(actions_np[:, :, :2]) * 0.6
        ctrl_view[:, :, 2] = np.tanh(actions_np[:, :, 2]) * 0.5
        if phase == "prep":
            ctrl_view[:, 2:, :] = 0.0
        assign_field(mjw_data.ctrl, ctrl)
        mjw.step(mjw_model, mjw_data)
        qpos_next = mjw_data.qpos.numpy()
        agent_next = qpos_next[:, slots["agents"]].reshape(worlds, 4, 4).astype(np.float32)
        if phase == "seek":
            reward, hiders_seen = visibility_reward(agent_next, occluders)
            caught = hiders_seen.any(axis=1).astype(np.float32)
            seen_rate = hiders_seen.mean(axis=1).astype(np.float32)
        else:
            reward = np.zeros((worlds, 4), dtype=np.float32)
            caught = np.zeros((worlds,), dtype=np.float32)
            seen_rate = np.zeros((worlds,), dtype=np.float32)
        for key, val in [
            ("self", self_obs), ("lidar", lidar), ("entities", entities), ("masks", masks), ("actions", actions_np),
            ("logp", logp.detach().cpu().numpy()), ("values", value.detach().cpu().numpy()),
            ("rewards", reward), ("dones", np.zeros((worlds, 4), dtype=np.float32)), ("caught", caught),
            ("hider_seen_rate", seen_rate),
        ]:
            buf[key].append(val)
    return {k: np.asarray(v, dtype=np.float32) for k, v in buf.items()}


def rollout_frame(step_idx, phase, agent_state, box_state, ramp_state, reward, hiders_seen, visible):
    agents = []
    for idx in range(agent_state.shape[0]):
        agents.append({
            "name": f"agent{idx}",
            "team": "hider" if idx < 2 else "seeker",
            "pos": [
                round(float(agent_state[idx, 0]), 5),
                round(float(agent_state[idx, 1]), 5),
                0.0,
            ],
            "yaw": round(float(agent_state[idx, 3]), 5),
            "seen": bool(hiders_seen[idx]) if idx < 2 else False,
            "seeing_hider": bool(visible[idx, :2].any()) if idx >= 2 else False,
        })
    boxes = [
        {
            "name": f"moveable_box{i}",
            "pos": [round(float(st[0]), 5), round(float(st[1]), 5), 0.0],
            "yaw": round(float(st[3]), 5),
        }
        for i, st in enumerate(box_state)
    ]
    ramps = [
        {
            "name": f"ramp{i}",
            "pos": [round(float(st[0]), 5), round(float(st[1]), 5), 0.0],
            "yaw": round(float(st[3]), 5),
        }
        for i, st in enumerate(ramp_state)
    ]
    return {
        "step": int(step_idx),
        "prep": phase == "prep",
        "reward": np.asarray(reward, dtype=float).round(5).tolist(),
        "visibility": {
            "agent_agent": visible.astype(int).tolist(),
            "seeker_hider": visible[2:, :2].astype(int).tolist(),
            "hiders_seen": hiders_seen.astype(int).tolist(),
            "any_hider_seen": bool(hiders_seen.any()),
        },
        "agents": agents,
        "boxes": boxes,
        "ramps": ramps,
    }


def export_policy_rollout(mjw, mjw_model, mjw_data, mjm, model, device, qpos0, qvel0, ctrl0, slots, steps, prep_steps, occluders, source, out_path):
    worlds = tuple(mjw_data.qpos.shape)[0]
    if worlds < 1:
        raise ValueError("rollout export requires at least one world")
    set_batched_field(mjw_data.qpos, qpos0)
    set_batched_field(mjw_data.qvel, qvel0)
    if mjm.nu:
        set_batched_field(mjw_data.ctrl, ctrl0)
    locked = np.zeros((worlds, slots["objects"].shape[0]), dtype=bool)
    frames = []
    total_reward = np.zeros((4,), dtype=np.float32)
    for step_idx in range(steps + 1):
        phase = "prep" if step_idx < prep_steps else "seek"
        qpos = mjw_data.qpos.numpy()
        qvel = mjw_data.qvel.numpy()
        agent_state = qpos[:, slots["agents"]].reshape(worlds, 4, 4).astype(np.float32)
        box_state = qpos[:, slots["boxes"]].reshape(worlds, slots["boxes"].shape[0], 4).astype(np.float32)
        ramp_state = qpos[:, slots["ramps"]].reshape(worlds, slots["ramps"].shape[0], 4).astype(np.float32)
        visible = agent_visibility(agent_state[:1], occluders)[0]
        reward, hiders_seen_batch = visibility_reward(agent_state[:1], occluders) if phase == "seek" else (
            np.zeros((1, 4), dtype=np.float32),
            np.zeros((1, 2), dtype=bool),
        )
        if phase == "seek":
            total_reward += reward[0]
        frames.append(rollout_frame(step_idx, phase, agent_state[0], box_state[0], ramp_state[0], reward[0], hiders_seen_batch[0], visible))
        if step_idx == steps:
            break
        object_state = qpos[:, slots["objects"]].reshape(worlds, slots["objects"].shape[0], 4).astype(np.float32)
        self_obs, lidar, entities, masks = build_obs(agent_state, box_state, ramp_state, phase, occluders)
        with __import__("torch").no_grad():
            mean, _ = model(to_torch(self_obs, device), to_torch(lidar, device), to_torch(entities, device), to_torch(masks, device))
        actions_np = mean.detach().cpu().numpy()
        if phase == "prep":
            actions_np[:, 2:, :] = 0.0
        qpos, qvel, locked = manipulate_objects(qpos, qvel, slots["objects"], agent_state, object_state, actions_np, locked, phase)
        assign_field(mjw_data.qpos, qpos)
        assign_field(mjw_data.qvel, qvel)
        ctrl = np.zeros((worlds, mjm.nu), dtype=np.float32)
        ctrl_view = ctrl[:, :12].reshape(worlds, 4, 3)
        ctrl_view[:, :, :2] = np.tanh(actions_np[:, :, :2]) * 0.6
        ctrl_view[:, :, 2] = np.tanh(actions_np[:, :, 2]) * 0.5
        if phase == "prep":
            ctrl_view[:, 2:, :] = 0.0
        assign_field(mjw_data.ctrl, ctrl)
        mjw.step(mjw_model, mjw_data)
    payload = {
        "source": {**source, "policy": "mjwarp_ppo_checkpoint", "steps_requested": steps},
        "total_reward": total_reward.round(5).tolist(),
        "frames": frames,
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(payload, indent=2))
    return payload


def compute_gae(rewards, values, gamma=0.99, lam=0.95):
    adv = np.zeros_like(rewards, dtype=np.float32)
    lastgaelam = np.zeros(rewards.shape[1:], dtype=np.float32)
    next_value = np.zeros(rewards.shape[1:], dtype=np.float32)
    for t in reversed(range(rewards.shape[0])):
        delta = rewards[t] + gamma * next_value - values[t]
        lastgaelam = delta + gamma * lam * lastgaelam
        adv[t] = lastgaelam
        next_value = values[t]
    return adv, adv + values


def ppo_update(model, optimizer, batch, device, epochs, minibatch, clip_coef, vf_coef, ent_coef):
    import torch

    T, W, A = batch["rewards"].shape
    adv, ret = compute_gae(batch["rewards"], batch["values"])
    adv = (adv - adv.mean()) / (adv.std() + 1e-6)
    flat = {
        "self": batch["self"].reshape(T * W * A, SELF_DIM),
        "lidar": batch["lidar"].reshape(T * W * A, LIDAR_DIM),
        "entities": batch["entities"].reshape(T * W * A, batch["entities"].shape[3], ENTITY_DIM),
        "masks": batch["masks"].reshape(T * W * A, batch["masks"].shape[3]),
        "actions": batch["actions"].reshape(T * W * A, ACTION_DIM),
        "logp": batch["logp"].reshape(T * W * A),
        "adv": adv.reshape(T * W * A),
        "ret": ret.reshape(T * W * A),
    }
    n = flat["logp"].shape[0]
    idx = np.arange(n)
    stats = {}
    for _ in range(epochs):
        np.random.shuffle(idx)
        for start in range(0, n, minibatch):
            mb = idx[start:start + minibatch]
            mean, value = model(to_torch(flat["self"][mb], device), to_torch(flat["lidar"][mb], device), to_torch(flat["entities"][mb], device), to_torch(flat["masks"][mb], device))
            dist = torch.distributions.Normal(mean, model.log_std.exp())
            new_logp = dist.log_prob(to_torch(flat["actions"][mb], device)).sum(-1)
            ratio = torch.exp(new_logp - to_torch(flat["logp"][mb], device))
            adv_t = to_torch(flat["adv"][mb], device)
            pg_loss = -torch.min(adv_t * ratio, adv_t * torch.clamp(ratio, 1 - clip_coef, 1 + clip_coef)).mean()
            v_loss = 0.5 * (value - to_torch(flat["ret"][mb], device)).pow(2).mean()
            entropy = dist.entropy().sum(-1).mean()
            loss = pg_loss + vf_coef * v_loss - ent_coef * entropy
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            stats = {"loss": float(loss.detach().cpu()), "pg_loss": float(pg_loss.detach().cpu()), "v_loss": float(v_loss.detach().cpu()), "entropy": float(entropy.detach().cpu())}
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("state_json")
    parser.add_argument("--worlds", type=int, default=64)
    parser.add_argument("--updates", type=int, default=8)
    parser.add_argument("--horizon", type=int, default=80)
    parser.add_argument("--prep-fraction", type=float, default=0.4)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--minibatch", type=int, default=512)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--entropy-coef", type=float, default=0.01)
    parser.add_argument("--out", default="mjwarp_train_smoke.json")
    parser.add_argument("--checkpoint", default="mjwarp_policy.pt")
    parser.add_argument("--rollout-out", default=None)
    parser.add_argument("--rollout-steps", type=int, default=96)
    args = parser.parse_args()

    import torch
    # Initialize Torch CUDA first. The previous segfault happened before any
    # rollout logging when both Torch and Warp initialized CUDA implicitly.
    device = torch.device("cuda")
    torch.empty(1, device=device)
    print("train: torch cuda initialized", flush=True)
    print("train: importing mujoco/mjwarp/warp", flush=True)
    import mujoco
    import mujoco_warp as mjw
    import warp as wp
    print("train: imported mujoco/mjwarp/warp", flush=True)

    torch.manual_seed(0)
    model = make_model(args.hidden).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    print("train: model initialized", flush=True)
    wp.init()
    print("train: warp initialized", flush=True)
    payload = json.loads(Path(args.state_json).read_text())
    mjm = mujoco.MjModel.from_xml_string(payload["xml"])
    qpos0 = decode_array(payload["state"]["qpos"])
    qvel0 = decode_array(payload["state"]["qvel"])
    ctrl0 = decode_array(payload["state"]["ctrl"])
    boxes = qpos_slots_by_prefix(mjm, "moveable_box")
    ramps = qpos_slots_by_prefix(mjm, "ramp")
    slots = {
        "agents": qpos_slots_by_prefix(mjm, "agent"),
        "boxes": boxes,
        "ramps": ramps,
        "objects": np.concatenate([boxes, ramps], axis=0) if ramps.size else boxes,
    }
    occluders = static_occluders(mjm)
    mjw_model = mjw.put_model(mjm)
    mjw_data = mjw.make_data(mjm, nworld=args.worlds, nconmax=max(2048, mjm.ngeom * 128), njmax=8192)
    prep_steps = int(round(args.horizon * args.prep_fraction))
    history = []
    for update in range(args.updates):
        batch = collect_rollout(mjw, mjw_model, mjw_data, mjm, model, device, qpos0, qvel0, ctrl0, slots, args.horizon, prep_steps, occluders)
        stats = ppo_update(model, optimizer, batch, device, args.epochs, args.minibatch, 0.2, 0.5, args.entropy_coef)
        seek_rewards = batch["rewards"][prep_steps:]
        hider_rewards = seek_rewards[:, :, :2]
        seeker_rewards = seek_rewards[:, :, 2:]
        item = {
            "update": update,
            "mean_visibility_selfplay_reward": round(float(seek_rewards.mean()), 6),
            "mean_hider_reward": round(float(hider_rewards.mean()), 6),
            "mean_seeker_reward": round(float(seeker_rewards.mean()), 6),
            "caught_fraction": round(float(batch["caught"][prep_steps:].mean()), 6),
            "hider_seen_rate": round(float(batch["hider_seen_rate"][prep_steps:].mean()), 6),
            **{k: round(v, 6) for k, v in stats.items()},
        }
        history.append(item)
        print(item, flush=True)
    Path(args.checkpoint).parent.mkdir(parents=True, exist_ok=True)
    torch.save({"model": model.state_dict(), "source": payload["source"], "arch": "entity_attention_ppo"}, args.checkpoint)
    rollout_path = args.rollout_out
    if rollout_path:
        export_policy_rollout(
            mjw, mjw_model, mjw_data, mjm, model, device, qpos0, qvel0, ctrl0, slots,
            args.rollout_steps, int(round(args.rollout_steps * args.prep_fraction)), occluders,
            payload["source"], rollout_path,
        )
    result = {
        "source": payload["source"],
        "worlds": args.worlds,
        "updates": args.updates,
        "horizon": args.horizon,
        "objective": "paper_visibility_selfplay_entity_attention_ppo",
        "paper_components": [
            "shared blue/red self-play policy",
            "preparation phase with seekers frozen",
            "frontal vision cone and wall line-of-sight mask",
            "circular 1D lidar convolution stream",
            "entity embeddings for agents boxes and ramps",
            "masked entity attention",
            "movement grabbing and locking action heads",
        ],
        "checkpoint": args.checkpoint,
        "rollout": rollout_path,
        "history": history,
        "prep_fraction": args.prep_fraction,
        "lr": args.lr,
        "hidden": args.hidden,
        "entropy_coef": args.entropy_coef,
        "final_mean_visibility_selfplay_reward": history[-1]["mean_visibility_selfplay_reward"] if history else 0.0,
        "final_mean_hider_reward": history[-1]["mean_hider_reward"] if history else 0.0,
        "final_mean_seeker_reward": history[-1]["mean_seeker_reward"] if history else 0.0,
        "final_caught_fraction": history[-1]["caught_fraction"] if history else 0.0,
        "final_hider_seen_rate": history[-1]["hider_seen_rate"] if history else 0.0,
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
