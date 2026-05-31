/* Autoresearch — Compare page.
   ONE evolution tree as the main panel, with TWO branches highlighted on it
   (A blue, B amber) and their divergence marked. BELOW: side-by-side run
   snapshots showing how each branch's algorithm actually executes. Right: the
   branch diff panel. Click any node to reassign the active branch. */
(function () {
  const { useState, useRef, useMemo, Fragment } = React;
  const RUNS = window.EVO_RUNS;
  const BY = window.EVO_RUN_BY_ID;
  const fmt = (n) => {
    if (n == null) return '—';
    if (typeof n !== 'number') return String(n);
    if (!Number.isFinite(n)) return String(n);
    const abs = Math.abs(n);
    const maximumFractionDigits = Number.isInteger(n) ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
    return n.toLocaleString(undefined, { maximumFractionDigits });
  };
  const mmss = (t) => String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(Math.round(t % 60)).padStart(2, '0');
  const fitVar = (f) => `var(--fit-${f})`;
  const isMaximize = (world) => String((world.meta && world.meta.direction) || 'minimize').toLowerCase() === 'maximize';
  const isMatmulDomain = (world) => String((world.meta && world.meta.domain) || '').toLowerCase().includes('matmul');
  const betterScore = (world, a, b) => isMaximize(world) ? b.score - a.score : a.score - b.score;
  const parentOf = (node) => node && (node.displayParent || node.parent);

  function lineageArr(world, nodeId) { const a = []; let c = world.nodes.find((n) => n.id === nodeId); while (c) { a.unshift(c); const p = parentOf(c); c = p ? world.nodes.find((n) => n.id === p) : null; } return a; }
  function gen1Of(world, node) { let c = node; while (c && c.gen > 1) { const p = parentOf(c); c = p ? world.nodes.find((n) => n.id === p) : null; } return c; }

  // Pick two branches that come from a COMMON tree: find the deepest fork point
  // (a node whose subtree splits into ≥2 children that each lead to an accepted
  // candidate) and return the best leaf from two different children. This makes
  // A and B share a real ancestor at gen > 0 instead of only the root.
  function bestBranchPair(world) {
    const nodes = world.nodes;
    const maximize = isMaximize(world);
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const childrenOf = new Map();
    nodes.forEach((n) => { const p = parentOf(n); if (p && byId.has(p)) { (childrenOf.get(p) || childrenOf.set(p, []).get(p)).push(n); } });
    const scored = (n) => n.outcome === 'accept' && n.score != null;

    // For each node, the best (lowest-score) accepted leaf in its subtree.
    const bestInSubtree = new Map();
    const visit = (n) => {
      if (bestInSubtree.has(n.id)) return bestInSubtree.get(n.id);
      let best = scored(n) ? n : null;
      (childrenOf.get(n.id) || []).forEach((c) => {
        const cb = visit(c);
        if (cb && (!best || (maximize ? cb.score > best.score : cb.score < best.score))) best = cb;
      });
      bestInSubtree.set(n.id, best);
      return best;
    };
    nodes.forEach(visit);

    // Among all fork nodes, prefer the deepest (max gen); break ties by the
    // combined quality of its two best diverging child-subtrees.
    let pick = null;
    nodes.forEach((f) => {
      const kids = (childrenOf.get(f.id) || [])
        .map((c) => ({ c, leaf: bestInSubtree.get(c.id) }))
        .filter((x) => x.leaf)
        .sort((a, b) => maximize ? b.leaf.score - a.leaf.score : a.leaf.score - b.leaf.score);
      if (kids.length < 2) return;
      const a = kids[0].leaf, b = kids[1].leaf;
      if (a.id === b.id) return;
      const cand = { forkGen: f.gen, sum: a.score + b.score, a: a.id, b: b.id };
      if (!pick || cand.forkGen > pick.forkGen || (cand.forkGen === pick.forkGen && (maximize ? cand.sum > pick.sum : cand.sum < pick.sum))) pick = cand;
    });
    if (pick) return { a: pick.a, b: pick.b };

    // Fallback (flat/degenerate tree): global best vs next-best accepted leaf.
    const acc = nodes.filter(scored).sort((x, y) => betterScore(world, x, y));
    const best = byId.get(world.meta.bestNode) || acc[0] || nodes[0];
    const b = acc.find((n) => n.id !== best.id) || best;
    return { a: best.id, b: b.id };
  }
  function genIR(node) {
    const m = (node.candidate.match(/(\d+)x(\d+)x(\d+)/) || [null, '4', '2', '1']);
    return [`; ${node.candidate}`, `panel = tile(${m[1]}, ${m[2]})`, `for (i,j) in panels(C, panel):`,
      `  acc = zero(${m[1]}, ${m[2]})`, `  for k in 0..16 step ${m[3]}:`, `    acc = fma(A[i,k], B[k,j], acc)`,
      node.family === 'lifetime' ? `  free_dead(T)   ; reuse` : `  ; no lifetime reuse`, `  store C[i,j] = acc`].join('\n');
  }

  // ---- main evolution tree with two highlighted branches ----
  function EvoTreeTwo({ world, aNode, bNode, cA, cB, active, onPick }) {
    const W = 1320, H = 640, pad = 54;
    const panelRef = useRef(null);
    const [hover, setHover] = useState(null);
    const dom = useMemo(() => {
      const values = world.nodes.map((n) => n.score).concat([world.meta.baseline, world.meta.best, world.meta.target])
        .filter((v) => typeof v === 'number' && Number.isFinite(v));
      if (!values.length) values.push(0, 1);
      const low = Math.min(...values);
      const high = Math.max(...values);
      return { low, high, span: Math.max(1e-9, high - low), worst: isMaximize(world) ? low : high, tMax: world.meta.tMax };
    }, [world]);
    const scoreY = (score) => {
      const sc = Math.max(dom.low, Math.min(dom.high, score));
      const rank = isMaximize(world) ? (dom.high - sc) / dom.span : (sc - dom.low) / dom.span;
      return pad + rank * (H - 2 * pad);
    };
    const pos = useMemo(() => {
      const ls = {};
      world.nodes.forEach((n, i) => {
        if (n.score != null) ls[n.id] = n.score;
        else {
          const p = n.parent ? ls[n.parent] : dom.worst;
          const nudge = (n.outcome === 'reject' ? 0.12 : 0.04) * dom.span * (isMaximize(world) ? -1 : 1);
          const jitter = ((i % 7) - 3) * dom.span * 0.01;
          ls[n.id] = Math.max(dom.low, Math.min(dom.high, (p == null ? dom.worst : p) + nudge + jitter));
        }
      });
      const o = {};
      world.nodes.forEach((n) => { o[n.id] = { x: pad + (n.tProposed / dom.tMax) * (W - 2 * pad), y: scoreY(ls[n.id]) }; });
      return o;
    }, [world, dom]);
    const linA = useMemo(() => new Set(lineageArr(world, aNode).map((n) => n.id)), [world, aNode]);
    const linB = useMemo(() => new Set(lineageArr(world, bNode).map((n) => n.id)), [world, bNode]);
    const diverge = useMemo(() => { const arrA = lineageArr(world, aNode); let last = arrA[0] || world.nodes[0]; for (const n of arrA) { if (linB.has(n.id)) last = n; else break; } return last; }, [world, aNode, bNode]);
    const T = world.meta.tMax;
    const edge = (a, b) => {
      const pa = pos[a], pb = pos[b];
      if (!pa || !pb) return null;
      const mx = (pa.x + pb.x) / 2;
      return `M${pa.x} ${pa.y} C ${mx} ${pa.y}, ${mx} ${pb.y}, ${pb.x} ${pb.y}`;
    };

    const onMove = (e) => { if (hover) { const r = panelRef.current.getBoundingClientRect(); setHover((h) => h && ({ ...h, mx: e.clientX - r.left, my: e.clientY - r.top })); } };

    return (
      <div className="evo-tree" ref={panelRef} onPointerMove={onMove}>
        <svg className="evo-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          {/* edges */}
          {world.nodes.map((n) => {
            const p = parentOf(n);
            if (!p) return null;
            const path = edge(p, n.id);
            if (!path) return null;
            const inA = linA.has(n.id) && linA.has(p), inB = linB.has(n.id) && linB.has(p);
            const both = inA && inB;
            const col = both ? 'var(--ink-2)' : inA ? cA : inB ? cB : (n.score != null ? fitVar(n.fit) : (n.outcome === 'reject' ? 'var(--bad)' : 'var(--line-strong)'));
            return <path key={'e' + n.id} d={path} fill="none" stroke={col} strokeWidth={(inA || inB) ? 2.6 : 1} opacity={(inA || inB) ? 0.98 : (n.score != null ? 0.26 : 0.18)} />;
          })}
          {/* nodes */}
          {world.nodes.map((n) => {
            const st = world.fns.statusAt(n, T); const p = pos[n.id];
            if (!p) return null;
            const onA = linA.has(n.id), onB = linB.has(n.id); const on = onA || onB;
            let r, fill, stroke = 'none';
            if (st === 'verified') { r = 3.4 + (n.fit || 0) * 1.0; fill = fitVar(n.fit); }
            else if (st === 'rejected') { r = 2.8; fill = 'var(--bg-canvas)'; stroke = 'var(--bad)'; }
            else { r = 2.6; fill = 'var(--bg-canvas)'; stroke = 'var(--line-strong)'; }
            const isLeafA = n.id === aNode, isLeafB = n.id === bNode, isDiv = n.id === diverge.id;
            return (
              <g key={n.id} transform={`translate(${p.x} ${p.y})`} style={{ cursor: 'pointer' }} opacity={on ? 1 : 0.4}
                onClick={() => onPick(n.id)}
                onPointerEnter={() => { const rect = panelRef.current.getBoundingClientRect(); setHover({ n, st, mx: p.x / W * rect.width, my: p.y / H * rect.height }); }}
                onPointerLeave={() => setHover(null)}>
                {(isLeafA || isLeafB) ? <circle r={r + 4.5} fill="none" stroke={isLeafA ? cA : cB} strokeWidth={2.2} /> : null}
                {isDiv && !isLeafA && !isLeafB ? <rect x={-(r + 3)} y={-(r + 3)} width={2 * (r + 3)} height={2 * (r + 3)} fill="none" stroke="var(--ink-2)" strokeWidth={1.4} transform="rotate(45)" /> : null}
                <circle r={r} fill={fill} stroke={stroke} strokeWidth={stroke !== 'none' ? 1.4 : 0} />
              </g>
            );
          })}
          {/* axis chrome */}
          <text x={pad} y={22} className="og-axis">better ↑</text>
          <text x={pad} y={H - 14} className="og-axis">worse ↓</text>
          <text x={W - pad} y={H - 14} textAnchor="end" className="og-axis">time →</text>
        </svg>
        {/* overlays */}
        <div className="evo-legend">
          <span className="og-leg"><i style={{ background: cA }} />Branch A</span>
          <span className="og-leg"><i style={{ background: cB }} />Branch B</span>
          <span className="og-leg"><b style={{ width: 8, height: 8, border: '1.4px solid var(--ink-2)', transform: 'rotate(45deg)', display: 'inline-block', borderRadius: 0 }} />{'diverges · gen ' + diverge.gen}</span>
        </div>
        <div className="evo-active mono">assigning: <span style={{ color: active === 'a' ? cA : cB, fontWeight: 700 }}>{'Branch ' + active.toUpperCase()}</span> — click a node</div>
        {hover ? (
          <div className="ctree-tip" style={{ left: Math.min(hover.mx + 12, (panelRef.current ? panelRef.current.clientWidth : 500) - 184), top: hover.my + 10 }}>
            <div className="ctt-title">{hover.n.title}</div>
            <div className="ctt-meta mono">{hover.n.id + ' · gen ' + hover.n.gen + ' · ' + hover.n.family}</div>
            <div className="ctt-score mono" style={{ color: hover.n.score != null ? fitVar(hover.n.fit) : 'var(--ink-3)' }}>{hover.n.score != null ? fmt(hover.n.score) + ' ' + (world.meta.metric || 'score') : hover.st}</div>
          </div>
        ) : null}
      </div>
    );
  }

  // ---- run snapshot below ----
  function artifactUrl(path) {
    return '/api/artifact?path=' + encodeURIComponent(path);
  }

  function valueText(value) {
    if (value == null) return '—';
    if (typeof value === 'number') return fmt(value);
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function labelText(value) {
    return String(value || '').replace(/_/g, ' ');
  }

  function SnapBlock({ title, children }) {
    if (!children) return null;
    return (
      <div className="snap-block">
        <div className="block-label">{title}</div>
        {children}
      </div>
    );
  }

  function ArtifactMedia({ artifact }) {
    const images = artifact && Array.isArray(artifact.images) ? artifact.images : [];
    const videos = artifact && Array.isArray(artifact.videos) ? artifact.videos : [];
    if (!images.length && !videos.length) return null;
    const solo = images.length + videos.length === 1;
    return (
      <div className={'artifact-media' + (solo ? ' solo' : '')}>
        {videos.map((file) => <video key={file.path} src={artifactUrl(file.path)} controls muted loop playsInline />)}
        {images.map((file) => <a key={file.path} href={artifactUrl(file.path)} target="_blank" rel="noreferrer">
          <img src={artifactUrl(file.path)} alt={file.name} />
        </a>)}
      </div>
    );
  }

  function VoxelMini({ artifact }) {
    const body = artifact && artifact.body;
    const grid = body && Array.isArray(body.grid) ? body.grid : null;
    if (!grid || !grid.length) return null;
    const cols = Math.max(1, ...grid.map((row) => Array.isArray(row) ? row.length : 0));
    const legend = body.legend || {};
    return (
      <div className="snap-voxel-wrap">
        <div className="voxel-grid snap-voxel" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {grid.flatMap((row, y) => row.map((cell, x) => (
            <span key={y + ':' + x} className={'voxel v' + cell} title={(legend[String(cell)] || cell) + ' · ' + x + ',' + y} />
          )))}
        </div>
        {Object.keys(legend).length ? <div className="voxel-legend">
          {Object.entries(legend).map(([k, v]) => (
            <span key={k} className="voxel-legend-item"><span className={'voxel-swatch v' + k} />{v}</span>
          ))}
        </div> : null}
      </div>
    );
  }

  function RunRecord({ node }) {
    const rows = [
      ['submission', node.subId],
      ['verification', node.verId],
      ['decision', node.outcome],
      ['semantic', node.semantic],
      ['proposer', node.proposerRole || node.proposer],
      ['implementor', node.impl],
      ['verifier', node.verifier],
      ['proposed', node.tProposed == null ? null : mmss(node.tProposed)],
      ['claimed', node.tClaimed == null ? null : mmss(node.tClaimed)],
      ['submitted', node.tSubmitted == null ? null : mmss(node.tSubmitted)],
      ['verified', node.tVerified == null ? null : mmss(node.tVerified)],
    ].filter(([, v]) => v != null && v !== '');
    if (!rows.length) return null;
    return (
      <div className="snap-record">
        {rows.map(([k, v]) => <div key={k} className="prow"><span className="pk">{k}</span><span className="pv">{valueText(v)}</span></div>)}
      </div>
    );
  }

  function ArtifactMetrics({ artifact }) {
    const entries = Object.entries((artifact && artifact.metrics) || {}).filter(([, v]) => v != null);
    if (!entries.length) return null;
    return (
      <div className="artifact-metrics">
        {entries.map(([k, v]) => <div key={k} className="prow"><span className="pk">{labelText(k)}</span><span className="pv">{valueText(v)}</span></div>)}
      </div>
    );
  }

  function ArtifactFiles({ artifact }) {
    const files = artifact && Array.isArray(artifact.files) ? artifact.files : [];
    if (!files.length) return null;
    return (
      <div className="artifact-files">
        {files.map((file) => <a key={file.path} className="artifact-file" href={artifactUrl(file.path)} target="_blank" rel="noreferrer">
          <span className="artifact-kind">{file.kind}</span>
          <span className="artifact-name">{file.name}</span>
          {file.size != null ? <span className="artifact-size">{fmt(file.size) + ' B'}</span> : null}
        </a>)}
      </div>
    );
  }

  function CustomVisualizations({ node, world, speed }) {
    const known = new Set(['matmul_ir', 'matmul_playback', 'voxel_grid', 'metrics', 'media', 'artifact_media', 'rollout_media', 'artifact_files', 'artifact_bundle']);
    const views = ((world.meta && world.meta.visualizations) || []).filter((view) => view && !known.has(view.type));
    const registry = window.AutoresearchVisualizations || {};
    return views.map((view) => {
      const Comp = registry[view.type] || (view.component ? window[view.component] : null);
      if (!Comp) return null;
      return <SnapBlock key={view.type} title={view.label || labelText(view.type)}>
        <Comp node={node} view={view} app={world} speed={speed} artifactUrl={artifactUrl} fmt={fmt} />
      </SnapBlock>;
    });
  }

  function ArtifactSummary({ node, world, speed }) {
    const artifact = node && node.artifact ? node.artifact.details : null;
    if (!artifact) {
      return <div className="artifact-view snap-artifacts"><div className="snap-empty">No artifact recorded for this candidate.</div></div>;
    }
    const hasMedia = (artifact.images && artifact.images.length) || (artifact.videos && artifact.videos.length);
    const hasBody = artifact.body && Array.isArray(artifact.body.grid) && artifact.body.grid.length;
    const hasMetrics = artifact.metrics && Object.values(artifact.metrics).some((v) => v != null);
    const hasFiles = artifact.files && artifact.files.length;
    return (
      <div className="artifact-view snap-artifacts">
        {hasMedia ? <SnapBlock title="rollout"><ArtifactMedia artifact={artifact} /></SnapBlock> : null}
        {hasBody ? <SnapBlock title="body"><VoxelMini artifact={artifact} /></SnapBlock> : null}
        {hasMetrics ? <SnapBlock title="evaluation"><ArtifactMetrics artifact={artifact} /></SnapBlock> : null}
        <SnapBlock title="run record"><RunRecord node={node} /></SnapBlock>
        <CustomVisualizations node={node} world={world} speed={speed} />
        {hasFiles ? <SnapBlock title="artifacts"><ArtifactFiles artifact={artifact} /></SnapBlock> : null}
        {artifact.preview ? <SnapBlock title="preview"><pre className="artifact-preview">{artifact.preview}</pre></SnapBlock> : null}
      </div>
    );
  }

  function RunSnapshot({ label, color, node, world, speed }) {
    const p = parentOf(node);
    const parent = p ? world.nodes.find((n) => n.id === p) : null;
    const d = parent && parent.score != null && node.score != null ? node.score - parent.score : null;
    const deltaGood = d == null ? false : isMaximize(world) ? d > 0 : d < 0;
    const deltaBad = d == null ? false : isMaximize(world) ? d < 0 : d > 0;
    const RunPlayback = window.RunPlayback;
    const showMatmul = isMatmulDomain(world) && RunPlayback;
    const metric = world.meta.metric || 'score';
    return (
      <div className="snap">
        <div className="snap-head">
          <div className="snap-titlerow">
            <span className="mt-dot" style={{ background: color }} />
            <span className="snap-run" style={{ color }}>{label}</span>
            <span className="snap-gen mono">{node.id + ' · gen ' + node.gen}</span>
          </div>
          <div className="snap-title">{node.gen === 0 ? 'Baseline IR' : node.title}</div>
          <div className="snap-cand mono">{node.candidate}</div>
        </div>
        <div className="snap-metrics">
          <div className="snap-m"><span className="snap-mk">{metric}</span><span className="snap-mv mono">{fmt(node.score)}</span></div>
          <div className="snap-m"><span className="snap-mk">Δ step</span><span className="snap-mv mono" style={{ color: d == null ? 'var(--ink-3)' : deltaGood ? 'var(--ok)' : deltaBad ? 'var(--bad)' : 'var(--ink-3)' }}>{d == null ? '—' : (d < 0 ? '▼ ' : '▲ ') + fmt(Math.abs(d))}</span></div>
          <div className="snap-m"><span className="snap-mk">family</span><span className="snap-mv mono">{node.family}</span></div>
        </div>
        {showMatmul ? <div className="snap-run-wrap"><RunPlayback node={node} speed={speed} key={node.id} /></div> : null}
        <ArtifactSummary node={node} world={world} speed={speed} />
        {/* Real submitted code when available (from the journal artifact); fall
            back to a representative IR only for mock/codeless nodes. */}
        {showMatmul && node.code ? (
          <Fragment>
            <div className="block-label" style={{ margin: '4px 0 6px' }}>{'candidate code · ' + (node.codeLang || 'python')}</div>
            <pre className="well ir">{node.code}</pre>
          </Fragment>
        ) : showMatmul ? (
          <Fragment>
            <div className="block-label" style={{ margin: '4px 0 6px' }}>candidate IR · representative</div>
            <pre className="well ir">{genIR(node)}</pre>
          </Fragment>
        ) : null}
      </div>
    );
  }

  // ---- right diff panel ----
  function Section({ title, children, defaultOpen = true }) {
    const [open, setOpen] = useState(defaultOpen);
    return (
      <div className="sb-section">
        <div className={'sb-head' + (open ? '' : ' collapsed')} onClick={() => setOpen((o) => !o)}>
          <svg className="sb-chev" viewBox="0 0 16 16" fill="none"><path d="M5 6 L8 9.5 L11 6" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" /></svg>
          <h3>{title}</h3>
        </div>
        {open ? <div className="sb-body">{children}</div> : null}
      </div>
    );
  }
  function DiffRow({ k, a, b, f, lowerBetter, neutral }) {
    f = f || fmt; let aw = false, bw = false;
    if (!neutral && a !== b && typeof a === 'number') { if (lowerBetter) { aw = a < b; bw = b < a; } else { aw = a > b; bw = b > a; } }
    const va = f(a);
    const vb = f(b);
    return (
      <div className="drow">
        <span className="dk">{k}</span>
        <span className={'dv mono' + (aw ? ' win' : '')} title={String(va)}>{va}</span>
        <span className={'dv mono' + (bw ? ' win' : '')} title={String(vb)}>{vb}</span>
      </div>
    );
  }
  function BucketCompare({ na, nb, cA, cB }) {
    const order = ['mul', 'add', 'copy', 'load', 'store'];
    const seg = (buckets) => {
      if (!buckets) return <div className="bcmp-bar" />;
      const tot = order.reduce((s, k) => s + buckets[k], 0);
      if (!tot) return <div className="bcmp-bar" />;
      return <div className="bcmp-bar">{order.map((k, i) => <div key={k} className="bcmp-seg" style={{ width: (buckets[k] / tot * 100) + '%', background: `var(--fit-${i + 1})` }} title={k} />)}</div>;
    };
    return (
      <div>
        <div className="bcmp-row"><span className="bcmp-dot" style={{ background: cA }} />{seg(na.buckets)}</div>
        <div className="bcmp-row"><span className="bcmp-dot" style={{ background: cB }} />{seg(nb.buckets)}</div>
        <div className="bcmp-legend">{order.map((k, i) => <span key={k}><i style={{ background: `var(--fit-${i + 1})` }} />{k}</span>)}</div>
      </div>
    );
  }

  function App() {
    const [runId, setRunId] = useState(() => (RUNS && RUNS[0] ? RUNS[0].id : 'panel'));
    const run = BY[runId] || RUNS[0]; const world = run.world;
    const cA = 'var(--cmp-a)', cB = 'var(--cmp-b)';

    // default branches: two leaves that fork from a real shared ancestor in the
    // SAME tree (deepest fork), so A and B genuinely diverge instead of being
    // two separate gen-0 roots.
    const defaults = useMemo(() => bestBranchPair(world), [runId]);

    const [aNode, setANode] = useState(defaults.a);
    const [bNode, setBNode] = useState(defaults.b);
    const [active, setActive] = useState('b');
    React.useEffect(() => { setANode(defaults.a); setBNode(defaults.b); }, [runId]);

    const nodeA = world.nodes.find((n) => n.id === aNode) || world.nodes[0];
    const nodeB = world.nodes.find((n) => n.id === bNode) || world.nodes[0];
    const onPick = (id) => { if (active === 'a') setANode(id); else setBNode(id); };

    // divergence stats
    const arrA = lineageArr(world, aNode), arrB = lineageArr(world, bNode);
    const setB = new Set(arrB.map((n) => n.id)); let div = arrA[0]; for (const n of arrA) { if (setB.has(n.id)) div = n; else break; }
    const aOnly = arrA.length - (arrA.findIndex((n) => n.id === div.id) + 1);
    const bOnly = arrB.length - (arrB.findIndex((n) => n.id === div.id) + 1);

    // Every accepted+scored candidate is directly selectable for either branch,
    // best-first, so you can pick the exact two to compare at any time.
    const selectable = useMemo(() => world.nodes
      .filter((n) => n.outcome === 'accept' && n.score != null)
      .sort((x, y) => betterScore(world, x, y)), [runId]);

    // Chip = click to make this side active (then click a tree node), plus a
    // dropdown to choose the candidate for this branch directly.
    const setSide = (side, id) => { if (side === 'a') setANode(id); else setBNode(id); setActive(side); };
    const BranchChip = ({ side, node, color }) => (
      <div className={'bchip' + (active === side ? ' on' : '')} style={active === side ? { borderColor: color } : null} onClick={() => setActive(side)}>
        <span className="bchip-side" style={{ color }}>{side.toUpperCase()}</span>
        <span className="bchip-dot" style={{ background: color }} />
        <select className="bchip-select mono" value={node.id}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setSide(side, e.target.value)}
          title={'Choose branch ' + side.toUpperCase() + ' candidate'}>
          {selectable.map((n) => <option key={n.id} value={n.id}>{n.candidate + ' · ' + fmt(n.score) + ' · gen ' + n.gen}</option>)}
        </select>
        <span className="bchip-score mono">{fmt(node.score)}</span>
      </div>
    );

    return (
      <div className="capp">
        <header className="ctop">
          <div className="top-left">
            <a className="logo" href="index.html" title="Back to dashboard">
              <svg viewBox="0 0 34 34" width={24} height={24} fill="none">
                <circle cx={6} cy={17} r={3} fill="var(--fit-1)" /><circle cx={17} cy={8} r={2.6} fill="var(--fit-3)" />
                <circle cx={17} cy={26} r={2.6} fill="var(--fit-2)" /><circle cx={28} cy={6} r={3.4} fill="var(--cmp-a)" />
                <circle cx={28} cy={20} r={2.4} fill="var(--fit-4)" /><path d="M9 17 L14.6 9 M9 17 L14.6 25 M19.4 8 L26 6.5 M19.4 8 L26 19 M19.4 26 L26 27.5" stroke="var(--line-strong)" strokeWidth={1.2} />
              </svg>
              <span className="logo-name">Autoresearch</span>
            </a>
            <nav className="nav-tabs">
              <a className="nav-tab" href="index.html">Tree</a>
              <a className="nav-tab active" href="compare.html">Compare</a>
            </nav>
            <div className="prob">
              <span className="prob-name">Compare branches</span>
              <label className="run-pick inline">
                <span className="rp-side">evolution</span>
                <select value={runId} onChange={(e) => setRunId(e.target.value)}>
                  {RUNS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                </select>
              </label>
            </div>
          </div>
          <div className="branch-chips">
            <BranchChip side="a" node={nodeA} color={cA} />
            <BranchChip side="b" node={nodeB} color={cB} />
          </div>
        </header>

        <div className="cbody4">
          <div className="evo-main">
            <EvoTreeTwo world={world} aNode={aNode} bNode={bNode} cA={cA} cB={cB} active={active} onPick={onPick} />
            <div className="evo-snaps">
              <RunSnapshot label="Branch A" color={cA} node={nodeA} world={world} speed={1} />
              <RunSnapshot label="Branch B" color={cB} node={nodeB} world={world} speed={1} />
            </div>
          </div>

          <aside className="sidebar">
            <div className="sb-scroll">
              <Section title="Branches">
                <div className="drow dhead"><span className="dk" /><span className="dv" style={{ color: cA }}>A</span><span className="dv" style={{ color: cB }}>B</span></div>
                <DiffRow k={world.meta.metric || 'Score'} a={nodeA.score} b={nodeB.score} lowerBetter={!isMaximize(world)} />
                <DiffRow k="vs baseline" a={nodeA.score - world.meta.baseline} b={nodeB.score - world.meta.baseline} f={(x) => (x > 0 ? '+' : '') + fmt(x)} lowerBetter={false} />
                <DiffRow k="Generation" a={nodeA.gen} b={nodeB.gen} neutral />
                <DiffRow k="Family" a={nodeA.family} b={nodeB.family} f={(x) => String(x || '—').replace(/_/g, ' ')} neutral />
                <DiffRow k="Semantic" a={nodeA.semantic || '—'} b={nodeB.semantic || '—'} f={(x) => x} neutral />
              </Section>
              <Section title="Where they diverge">
                <div className="div-note">shared ancestor <span className="mono" style={{ color: 'var(--ink)' }}>{div.id}</span> at gen <b>{div.gen}</b></div>
                <div className="drow" style={{ marginTop: 8 }}><span className="dk">Steps after split</span><span className="dv mono" style={{ color: cA }}>{'+' + aOnly}</span><span className="dv mono" style={{ color: cB }}>{'+' + bOnly}</span></div>
                <div className="div-note" style={{ marginTop: 6 }}>shared score <span className="mono" style={{ color: 'var(--ink)' }}>{fmt(div.score)}</span></div>
              </Section>
              <Section title="Cost-bucket mix">
                <BucketCompare na={nodeA} nb={nodeB} cA={cA} cB={cB} />
              </Section>
            </div>
          </aside>
        </div>
      </div>
    );
  }

  ReactDOM.createRoot(document.getElementById('root')).render(<App />);
})();
