/* EvoTree — node-link canvas. x = time (proposed), y = score (better = up).
   Color & size encode fitness. Pan (drag) + zoom (wheel). Click a node to inspect. */
(function () {
  const { useState, useRef, useEffect, useMemo, useCallback } = React;
  let E = window.APP;
  window.addEventListener('autoresearch-world', (event) => { E = event.detail || window.APP; });

  const VW = 2200, VH = 860, PAD = 70;
  const fmtScore = (n) => {
    if (n == null) return '—';
    if (!Number.isFinite(n)) return String(n);
    const abs = Math.abs(n);
    const maximumFractionDigits = Number.isInteger(n) ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
    return n.toLocaleString(undefined, { maximumFractionDigits });
  };
  const ROLE_LANES = ['topline_manager', 'meta_agent', 'insight_generator', 'creative_explorer', 'global_searcher', 'implementor', 'verifier', 'researcher'];
  const finiteTime = (t, fallback = 0) => (typeof t === 'number' && Number.isFinite(t) ? t : fallback);
  const mmss = (t) => {
    const safe = Math.max(0, finiteTime(t));
    const total = Math.round(safe);
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return String(minutes).padStart(2, '0') + ':' + String(seconds).padStart(2, '0');
  };
  function hash01(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    return ((h >>> 0) % 10000) / 10000;
  }

  function buildTreeLayout(world) {
    const E = world || { meta: {}, nodes: [] };
    const IS_MAX = String(E.meta.direction || 'minimize').toLowerCase() === 'maximize';
    const scoreValues = E.nodes
      .map((n) => n.score)
      .concat([E.meta.baseline, E.meta.best, E.meta.target])
      .filter((v) => typeof v === 'number' && Number.isFinite(v));
    if (!scoreValues.length) scoreValues.push(0, 1);
    const SLOW = Math.min(...scoreValues);
    const SHIGH = Math.max(...scoreValues);
    const SCORE_SPAN = Math.max(1e-9, SHIGH - SLOW);
    const WORST_SCORE = IS_MAX ? SLOW : SHIGH;
    const BEST_SCORE = IS_MAX ? SHIGH : SLOW;
    const clampScore = (score) => Math.max(SLOW, Math.min(SHIGH, score));
    const scoreY = (score) => {
      const sc = clampScore(score);
      const rank = IS_MAX ? (SHIGH - sc) / SCORE_SPAN : (sc - SLOW) / SCORE_SPAN;
      return PAD + rank * (VH - 2 * PAD);
    };
    const scoreNudge = (amount) => (IS_MAX ? -amount : amount);
    const hasRealLineage = E.nodes.some((n) => n.parent);
    const parentFanout = {};
    const parentOf = (n) => n.displayParent || n.parent;
    E.nodes.forEach((n) => { const p = parentOf(n); if (p) parentFanout[p] = (parentFanout[p] || 0) + 1; });
    const maxParentFanout = Math.max(0, ...Object.values(parentFanout));
    const maxNodeGen = Math.max(0, ...E.nodes.map((n) => n.gen || 0));
    const scoreVariety = new Set(E.nodes.filter((n) => n.score != null).map((n) => n.score)).size;
    const useTreeLayout = hasRealLineage;
    const useBranchLaneLayout = useTreeLayout && !(maxNodeGen <= 2 && maxParentFanout > E.nodes.length * 0.35);
    const useLaneLayout = !hasRealLineage && scoreVariety <= 3;

  // stable layout (independent of scrub time)
  const LAYOUT = (() => {
    const ls = {};               // layoutScore per node id
    const byId = {};
    E.nodes.forEach((n) => (byId[n.id] = n));
    E.nodes.forEach((n, i) => {
      if (n.score != null) ls[n.id] = n.score;
      else {
        const p = n.parent ? ls[n.parent] : WORST_SCORE;
        // failed/in-flight nodes sit near their parent with a deterministic nudge
        const nudge = (n.outcome === 'reject' ? 0.12 : 0.04) * SCORE_SPAN;
        const jitter = ((i % 7) - 3) * SCORE_SPAN * 0.01;
        ls[n.id] = clampScore((p == null ? WORST_SCORE : p) + scoreNudge(nudge) + jitter);
      }
    });
    const branchY = {};
    if (useBranchLaneLayout) {
      const children = {};
      E.nodes.forEach((n) => { children[n.id] = []; });
      E.nodes.forEach((n) => { const p = parentOf(n); if (p && children[p]) children[p].push(n); });
      Object.values(children).forEach((arr) => arr.sort((a, b) => a.tProposed - b.tProposed || a.id.localeCompare(b.id)));
      const roots = E.nodes
        .filter((n) => {
          const p = parentOf(n);
          return !p || !byId[p];
        })
        .sort((a, b) => a.tProposed - b.tProposed || a.id.localeCompare(b.id));
      let cursor = 0;
      const seen = new Set();
      const walk = (n) => {
        if (seen.has(n.id)) {
          branchY[n.id] = branchY[n.id] == null ? cursor++ : branchY[n.id];
          return branchY[n.id];
        }
        seen.add(n.id);
        const kids = children[n.id] || [];
        if (!kids.length) {
          branchY[n.id] = cursor++;
          return branchY[n.id];
        }
        const ys = kids.map(walk);
        branchY[n.id] = ys.reduce((a, b) => a + b, 0) / ys.length;
        return branchY[n.id];
      };
      roots.forEach(walk);
      E.nodes.forEach((n) => { if (branchY[n.id] == null) branchY[n.id] = cursor++; });
      const maxY = Math.max(1, cursor - 1);
      E.nodes.forEach((n) => { branchY[n.id] = PAD + (branchY[n.id] / maxY) * (VH - 2 * PAD); });
    }

    const pos = {};
    const nodeTimes = E.nodes
      .map((n) => finiteTime(n.tVerified ?? n.tProposed, null))
      .filter((t) => t != null);
    const metaTMax = Math.max(1, finiteTime(E.meta.tMax, 1));
    const tMin = nodeTimes.length ? Math.min(...nodeTimes) : 0;
    const tMax = Math.max(metaTMax, nodeTimes.length ? Math.max(...nodeTimes) : 0);
    const tSpan = Math.max(1, tMax - tMin);
    const duplicateOffset = {};
    const scoreGroups = {};
    E.nodes.forEach((n) => {
      if (n.score == null) return;
      const key = String(n.score);
      (scoreGroups[key] ||= []).push(n);
    });
    Object.values(scoreGroups).forEach((group) => {
      if (group.length <= 1) {
        duplicateOffset[group[0].id] = 0;
        return;
      }
      group.sort((a, b) => {
        const ak = `${parentOf(a) || ''}|${a.family || ''}|${a.proposerRole || ''}|${a.tProposed}|${a.id}`;
        const bk = `${parentOf(b) || ''}|${b.family || ''}|${b.proposerRole || ''}|${b.tProposed}|${b.id}`;
        return ak.localeCompare(bk);
      });
      const spread = Math.min(170, 24 + Math.sqrt(group.length) * 10);
      group.forEach((n, i) => {
        duplicateOffset[n.id] = group.length === 1 ? 0 : ((i / (group.length - 1)) - 0.5) * spread;
      });
    });
    const genMax = Math.max(1, ...E.nodes.map((n) => n.gen || 0));
    E.nodes.forEach((n) => {
      const timeX = ((n.tVerified || n.tProposed || 0) - tMin) / tSpan;
      const genX = (n.gen || 0) / genMax;
      const xRatio = useTreeLayout ? (0.82 * genX + 0.18 * timeX) : timeX;
      const x = PAD + xRatio * (VW - 2 * PAD);
      let y;
      if (useBranchLaneLayout) {
        const jitter = (hash01(n.id) - 0.5) * 12;
        y = scoreY(ls[n.id]) + (duplicateOffset[n.id] || 0) + jitter;
      } else if (useLaneLayout) {
        const laneKey = n.proposerRole || n.family || 'unknown';
        let lane = ROLE_LANES.indexOf(laneKey);
        if (lane < 0) lane = Math.floor(hash01(laneKey) * ROLE_LANES.length);
        const laneH = (VH - 2 * PAD) / ROLE_LANES.length;
        const scoreOffset = n.score === E.meta.best ? -laneH * 0.18 : n.score === E.meta.baseline ? laneH * 0.18 : 0;
        y = PAD + laneH * (lane + 0.5) + (hash01(n.id) - 0.5) * laneH * 0.62 + scoreOffset;
      } else {
        const jitter = useTreeLayout ? (hash01(n.id) - 0.5) * 26 : 0;
        y = scoreY(ls[n.id]) + jitter;
      }
      y = Math.max(PAD, Math.min(VH - PAD, y));
      pos[n.id] = { x, y };
    });
    return { pos, ls, tMin, tSpan };
  })();
    return { LAYOUT, parentOf, IS_MAX, WORST_SCORE, BEST_SCORE, useBranchLaneLayout, useLaneLayout };
  }

  const fitVar = (f) => `var(--fit-${f})`;

  function nodeVisual(n, st) {
    // returns {r, fill, stroke, op}
    if (n.halted) return { r: 6, fill: 'var(--surface-0)', stroke: 'var(--bad)', op: 1 };
    if (st === 'verified') {
      const r = 4.5 + (n.fit || 0) * 1.35;
      return { r, fill: fitVar(n.fit), stroke: 'none', op: 1, glow: n.isFrontier };
    }
    if (st === 'rejected') return { r: 4, fill: 'var(--surface-0)', stroke: 'var(--bad)', op: 0.9 };
    if (st === 'claimed' || st === 'submitted') return { r: 5.5, fill: 'var(--surface-0)', stroke: 'var(--accent)', op: 1, working: true };
    if (st === 'queued') return { r: 3.6, fill: 'var(--surface-0)', stroke: 'var(--line-strong)', op: 0.85 };
    if (st === 'abandoned') return { r: 3.4, fill: 'var(--surface-2)', stroke: 'var(--line-strong)', op: 0.5 };
    return { r: 0, op: 0 };
  }

  function EvoTree({ T, selected, onSelect, density, scoreLabels, dimOffLineage }) {
    const world = window.APP || E;
    E = world;
    const tree = useMemo(() => buildTreeLayout(world), [world]);
    const { LAYOUT, parentOf, IS_MAX, WORST_SCORE, BEST_SCORE, useBranchLaneLayout, useLaneLayout } = tree;
    const wrapRef = useRef(null);
    const [view, setView] = useState({ k: 1, tx: 0, ty: 0, ready: false });
    const drag = useRef(null);
    const [hover, setHover] = useState(null);

    // fit to container on mount / resize
    const fit = useCallback(() => {
      const el = wrapRef.current; if (!el) return;
      const w = el.clientWidth, h = el.clientHeight;
      const k = Math.min(w / VW, h / VH) * 0.96;
      setView({ k, tx: (w - VW * k) / 2, ty: (h - VH * k) / 2, ready: true });
    }, []);
    const onWheel = useCallback((e) => {
      e.preventDefault();
      e.stopPropagation();
      const el = wrapRef.current; const rect = el.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      setView((v) => {
        const k = Math.max(0.35, Math.min(9, v.k * factor));
        const tx = mx - (mx - v.tx) * (k / v.k);
        const ty = my - (my - v.ty) * (k / v.k);
        return { ...v, k, tx, ty };
      });
    }, []);
    useEffect(() => {
      fit();
      const r = () => fit();
      const el = wrapRef.current;
      const wheel = (e) => onWheel(e);
      window.addEventListener('resize', r);
      if (el) el.addEventListener('wheel', wheel, { passive: false });
      return () => {
        window.removeEventListener('resize', r);
        if (el) el.removeEventListener('wheel', wheel);
      };
    }, [fit, onWheel]);
    const onDown = (e) => { drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false }; };
    const onMove = (e) => {
      if (!drag.current) return;
      const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.current.moved = true;
      setView((v) => ({ ...v, tx: drag.current.tx + dx, ty: drag.current.ty + dy }));
    };
    const onUp = () => { drag.current = null; };

    // visible nodes & edges at time T
    const { vis, edges, transferEdges } = useMemo(() => {
      const vis = [];
      E.nodes.forEach((n) => {
        if (n.tVerified != null && n.tVerified <= T) vis.push(n);
      });
      const set = new Set(vis.map((n) => n.id));
      const edges = [];
      vis.forEach((n) => {
        const p = parentOf(n);
        if (p && set.has(p)) edges.push([p, n.id, n]);
      });
      const transferEdges = (E.transferEdges || [])
        .filter((edge) => edge.donor && edge.to && set.has(edge.donor) && set.has(edge.to))
        .map((edge) => [edge.donor, edge.to, edge]);
      return { vis, edges, transferEdges };
    }, [T, world]);

    const selNode = selected ? E.nodes.find((n) => n.id === selected) : null;
    // lineage path for highlight
    const lineage = useMemo(() => {
      const ids = new Set(); let cur = selNode;
      while (cur) {
        ids.add(cur.id);
        const p = parentOf(cur);
        cur = p ? E.nodes.find((n) => n.id === p) : null;
      }
      return ids;
    }, [selected, world]);

    const P = LAYOUT.pos;
    const timeTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
      const t = LAYOUT.tMin + LAYOUT.tSpan * f;
      return { x: PAD + f * (VW - 2 * PAD), t };
    });
    const edgeCount = edges.length;
    const labelIds = useMemo(() => {
      const out = new Set();
      const sorted = [...E.nodes]
        .filter((n) => n.score != null && n.tVerified != null)
        .sort((a, b) => (a.tVerified - b.tVerified) || (IS_MAX ? b.score - a.score : a.score - b.score) || a.id.localeCompare(b.id));
      sorted.forEach((n, i) => {
        if (n.isFrontier || i === 0 || i === sorted.length - 1 || i % Math.max(2, Math.ceil(sorted.length / 8)) === 0) out.add(n.id);
      });
      return out;
    }, [world, IS_MAX]);
    function edgePath(a, b) {
      const pa = P[a], pb = P[b];
      const dx = Math.max(28, pb.x - pa.x);
      const c1 = pa.x + dx * 0.42;
      const c2 = pb.x - dx * 0.24;
      return `M${pa.x} ${pa.y} C ${c1} ${pa.y}, ${c2} ${pb.y}, ${pb.x} ${pb.y}`;
    }

    return (
      <div className="tree-host" ref={wrapRef}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerLeave={onUp}
        style={{ cursor: drag.current ? 'grabbing' : 'grab' }}>

        <svg width="100%" height="100%" style={{ opacity: view.ready ? 1 : 0 }}>
          <g transform={`translate(${view.tx} ${view.ty}) scale(${view.k})`}>
            {/* edges */}
            {edges.map(([a, b, n]) => {
              const st = E.fns.statusAt(n, T);
              const onLin = lineage.has(a) && lineage.has(b);
              const col = n.score != null ? fitVar(n.fit) : (st === 'rejected' ? 'var(--bad)' : 'var(--line-strong)');
              return <path
                key={'e' + b} d={edgePath(a, b)} fill="none"
                stroke={onLin ? 'var(--accent)' : col}
                strokeWidth={onLin ? 2.4 : (edgeCount > 250 ? 0.75 : 1.25)}
                opacity={onLin ? 0.95 : (edgeCount > 250 ? (n.score != null ? 0.18 : 0.12) : (n.score != null ? 0.42 : 0.3))}
                style={{ transition: 'opacity .4s, stroke .2s' }}
              />;
            })}
            {transferEdges.map(([a, b, edge]) => {
              if (!P[a] || !P[b]) return null;
              const mid = { x: (P[a].x + P[b].x) / 2, y: (P[a].y + P[b].y) / 2 };
              const path = edgePath(a, b);
              return <g key={'g' + edge.id}>
                <path
                  d={path}
                  fill="none"
                  stroke="var(--accent)"
                  strokeWidth={1.7}
                  strokeDasharray="5 5"
                  opacity={0.72}
                  vectorEffect="non-scaling-stroke"
                  pointerEvents="none"
                />
                <path
                  d={path}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={12}
                  vectorEffect="non-scaling-stroke"
                  style={{ cursor: 'help' }}
                  onPointerEnter={() => setHover({ transfer: edge, p: mid })}
                  onPointerLeave={() => setHover(null)}
                />
              </g>;
            })}
            {timeTicks.map((tick, i) => <g key={'tick' + i}>
              <line x1={tick.x} y1={PAD - 18} x2={tick.x} y2={VH - PAD + 18} stroke="var(--line-soft)" strokeWidth={1} opacity={0.65} />
              <text x={tick.x} y={VH - PAD + 36} textAnchor="middle" className="time-tick">{mmss(tick.t)}</text>
            </g>)}
            {/* nodes */}
            {vis.map((n) => {
              const st = E.fns.statusAt(n, T);
              const v = nodeVisual(n, st);
              if (!v.r) return null;
              const r = Math.max(2.8, v.r / Math.sqrt(Math.max(1, view.k)));
              const p = P[n.id];
              const isSel = n.id === selected;
              const dim = dimOffLineage && selected && !lineage.has(n.id);
              return <g
                key={n.id} transform={`translate(${p.x} ${p.y})`}
                style={{ cursor: 'pointer', transition: 'opacity .4s' }} opacity={dim ? 0.28 : v.op}
                onClick={(e) => { e.stopPropagation(); if (!drag.current || !drag.current.moved) onSelect(n.id, { shift: e.shiftKey }); }}
                onPointerEnter={() => setHover({ n, st, p })} onPointerLeave={() => setHover(null)}
              >
                {v.glow ? <circle r={r + 6 / Math.sqrt(Math.max(1, view.k))} fill="var(--accent-soft)" /> : null}
                <circle
                  r={r} fill={v.fill} stroke={v.glow ? 'var(--accent)' : v.stroke}
                  strokeWidth={v.glow ? 2 : (v.stroke !== 'none' ? 1.6 : 0)}
                  vectorEffect="non-scaling-stroke"
                  style={v.working ? { animation: 'nodepulse 1.6s ease-in-out infinite' } : null}
                />
                {isSel ? <circle r={r + 5 / Math.sqrt(Math.max(1, view.k))} fill="none" stroke="var(--accent)" strokeWidth={2} vectorEffect="non-scaling-stroke" /> : null}
                {/* score label on bigger verified nodes */}
                {(st === 'verified' && scoreLabels && labelIds.has(n.id) && view.k > 0.7)
                  ? <text y={-r - 8 / view.k} textAnchor="middle" className="node-label" style={{ fontSize: (11 / view.k) + 'px' }}>
                      {fmtScore(n.score)}</text>
                  : null}
              </g>;
            })}
          </g>
        </svg>

        {/* hover tooltip (screen-space) */}
        {hover && hover.transfer ? <div
          className="tree-tip transfer-tip"
          style={{ left: hover.p.x * view.k + view.tx + 14, top: hover.p.y * view.k + view.ty - 8 }}
        >
          <div className="tt-head">
            <span className="mono tt-id">{'gene transfer'}</span>
            <span className="pill" data-status="working">
              <span className="dot" />{'graft'}</span></div>
          <div className="tt-title">
            {(hover.transfer.donorFamily || 'branch') + ' → ' + (hover.transfer.recipientFamily || 'branch')}</div>
          <div className="tt-score mono">{'donor ' + hover.transfer.donor + ' → ' + hover.transfer.to}</div>
          {hover.transfer.transferred
            ? <div className="tt-meta mono">
                {[
                  hover.transfer.transferred.operator,
                  hover.transfer.transferred.reuse_goal,
                  hover.transfer.transferred.tile ? JSON.stringify(hover.transfer.transferred.tile) : null,
                ].filter(Boolean).join(' · ') || 'structural transfer'}</div>
            : null}
        </div> : hover ? <div
          className="tree-tip"
          style={{ left: hover.p.x * view.k + view.tx + 14, top: hover.p.y * view.k + view.ty - 8 }}
        >
          <div className="tt-head">
            <span className="mono tt-id">{hover.n.id}</span>
            <span className={`pill`} data-status={pillStatus(hover.st)}>
              <span className="dot" />{hover.st}</span></div>
          <div className="tt-title">{hover.n.title}</div>
          {hover.n.score != null && hover.st === 'verified'
            ? <div className="tt-score mono">{fmtScore(hover.n.score) + ' ' + (E.meta.metric || 'score')}</div>
            : <div className="tt-score mono" style={{ color: 'var(--ink-3)' }}>
                {hover.st === 'rejected' ? 'rejected · ' + (hover.n.semantic) : 'in progress…'}</div>}
        </div>
          : null}

        {/* axis chrome (static) */}
        <div className="axis axis-x">{'← earlier'}<span>{'time →'}</span></div>
        <div className="axis axis-y-top">{useBranchLaneLayout ? 'branch lanes' : useLaneLayout ? 'orchestration lanes' : 'better ↑'}</div>
        <div className="axis axis-y-bot">{useBranchLaneLayout ? 'score shown by color' : useLaneLayout ? 'score nudges within lanes' : 'worse ↓'}</div>
        {/* legend + controls */}
        <div className="tree-legend">
          <span className="eyebrow">{'fitness'}</span>
          <div className="legend-ramp">
            {[0,1,2,3,4,5,6].map((f) => <span key={f} style={{ background: fitVar(f) }} />)}</div>
          <span className="mono legend-cap">{fmtScore(WORST_SCORE)}</span>
          <span className="mono legend-cap" style={{color:'var(--fit-6)'}}>{fmtScore(BEST_SCORE)}</span></div>
        <div className="tree-ctrls">
          <button className="btn icon" title="fit" onClick={fit}>{'⤢'}</button>
          <button className="btn icon" title="zoom in" onClick={() => setView(v=>({...v,k:Math.min(9,v.k*1.2)}))}>{'+'}</button>
          <button className="btn icon" title="zoom out" onClick={() => setView(v=>({...v,k:Math.max(0.35,v.k/1.2)}))}>{'−'}</button></div>
      </div>
    );
  }

  function pillStatus(st) {
    if (st === 'verified') return 'ok';
    if (st === 'rejected') return 'bad';
    if (st === 'claimed' || st === 'submitted') return 'working';
    return 'idle';
  }

  window.EvoTree = EvoTree;
  window.TREE_LAYOUT = () => buildTreeLayout(window.APP || E).LAYOUT;
})();
