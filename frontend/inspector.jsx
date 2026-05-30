/* NodeInspector — fills the sidebar with Figma-style property sections
   when a node is selected (metrics, buckets, lineage, trace, run playback). */
(function () {
  const { useState, useMemo, useEffect } = React;
  let E = window.APP;
  window.addEventListener('autoresearch-world', () => { E = window.APP; });
  const Section = window.SBSection;
  const roleCol = window.evoRoleCol;
  const ROLE_LABEL = window.EVO_ROLE_LABEL;
  const mmss = window.evoMMSS;
  const fmt = (n) => {
    if (n == null) return '—';
    if (typeof n !== 'number') return String(n);
    if (!Number.isFinite(n)) return String(n);
    const abs = Math.abs(n);
    const maximumFractionDigits = Number.isInteger(n) ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
    return n.toLocaleString(undefined, { maximumFractionDigits });
  };
  const isMaximize = () => String((E.meta && E.meta.direction) || 'minimize').toLowerCase() === 'maximize';
  const isMatmulDomain = () => String((E.meta && E.meta.domain) || '').toLowerCase().includes('matmul');

  function statusPill(st) {
    const map = { verified: 'ok', rejected: 'bad', claimed: 'working', submitted: 'working', queued: 'idle', abandoned: 'dead' };
    const label = { verified: 'verified', rejected: 'rejected', claimed: 'building', submitted: 'in verification', queued: 'queued', abandoned: 'abandoned' };
    return (
      <span className="pill" data-status={map[st] || 'idle'}>
        <span className="dot" />{label[st] || st}
      </span>
    );
  }

  const BUCKET_COLORS = { mul: '--role-implementor', add: '--role-verifier', copy: '--role-explorer', load: '--role-researcher', store: '--role-searcher' };
  function BucketBar({ buckets }) {
    if (!buckets) return null;
    const order = ['mul', 'add', 'copy', 'load', 'store'];
    const total = order.reduce((s, k) => s + buckets[k], 0);
    if (!total) return null;
    return (
      <div>
        <div className="bucket-bar">
          {order.map((k) => <div key={k} className="bucket-seg" style={{ width: (buckets[k] / total * 100) + '%', background: `var(${BUCKET_COLORS[k]})` }} title={k} />)}
        </div>
        <div className="bucket-legend">
          {order.map((k) => (
            <div key={k} className="bl-item">
              <span className="bl-dot" style={{ background: `var(${BUCKET_COLORS[k]})` }} />
              <span className="bl-k">{k}</span>
              <span className="bl-v">{fmt(buckets[k])}</span>
              <span className="bl-pct">{Math.round(buckets[k] / total * 100) + '%'}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function genIR(node) {
    const m = (node.candidate.match(/(\d+)x(\d+)x(\d+)/) || [null, '4', '2', '1']);
    return [
      `; ${node.candidate}`,
      `def matmul16(A,B) -> C {`,
      `  panel = tile(${m[1]}, ${m[2]})`,
      `  for (i,j) in panels(C, panel):`,
      `    acc = zero(${m[1]}, ${m[2]})`,
      `    for k in 0..16 step ${m[3]}:`,
      `      acc = fma(A[i,k], B[k,j], acc)`,
      node.family === 'lifetime' ? `    free_dead(T)   ; reuse` : `    ; no reuse`,
      `    store C[i,j] = acc`,
      `}`,
    ].join('\n');
  }

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

  function nodeArtifact(node) {
    return node && node.artifact ? node.artifact.details : null;
  }

  function MetricRows({ metrics }) {
    const entries = Object.entries(metrics || {}).filter(([, v]) => v != null);
    if (!entries.length) return <div className="insp-meta">No recorded metrics.</div>;
    return (
      <div className="artifact-metrics">
        {entries.map(([k, v]) => (
          <div key={k} className="prow">
            <span className="pk">{labelText(k)}</span>
            <span className="pv">{valueText(v)}</span>
          </div>
        ))}
      </div>
    );
  }

  function VoxelBody({ artifact }) {
    const body = artifact && artifact.body;
    const grid = body && Array.isArray(body.grid) ? body.grid : null;
    if (!grid || !grid.length) return <div className="insp-meta">No voxel body artifact.</div>;
    const cols = Math.max(1, ...grid.map((row) => Array.isArray(row) ? row.length : 0));
    const legend = body.legend || {};
    return (
      <div className="artifact-view">
        <div className="voxel-grid" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {grid.flatMap((row, y) => row.map((cell, x) => (
            <span key={y + ':' + x} className={'voxel v' + cell} title={(legend[String(cell)] || cell) + ' · ' + x + ',' + y} />
          )))}
        </div>
        <div className="voxel-legend">
          {Object.entries(legend).map(([k, v]) => (
            <span key={k} className="voxel-legend-item">
              <span className={'voxel-swatch v' + k} />{v}
            </span>
          ))}
        </div>
      </div>
    );
  }

  function ArtifactFiles({ artifact }) {
    const files = artifact && Array.isArray(artifact.files) ? artifact.files : [];
    if (!files.length) return <div className="insp-meta">No artifact files.</div>;
    return (
      <div className="artifact-view">
        <ArtifactMedia artifact={artifact} empty={null} />
        <div className="artifact-files">
          {files.map((file) => (
            <a key={file.path} className="artifact-file" href={artifactUrl(file.path)} target="_blank" rel="noreferrer">
              <span className="artifact-kind">{file.kind}</span>
              <span className="artifact-name">{file.name}</span>
              {file.size != null ? <span className="artifact-size">{fmt(file.size) + ' B'}</span> : null}
            </a>
          ))}
        </div>
        {artifact.preview ? <pre className="artifact-preview">{artifact.preview}</pre> : null}
      </div>
    );
  }

  function ArtifactMedia({ artifact, empty = <div className="insp-meta">No media artifact.</div> }) {
    const images = artifact && Array.isArray(artifact.images) ? artifact.images : [];
    const videos = artifact && Array.isArray(artifact.videos) ? artifact.videos : [];
    if (!images.length && !videos.length) return empty;
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

  function VerificationRecord({ node }) {
    return (
      <div className="well ver-rec">
        <div>submission  <span style={{ color: 'var(--ink)' }}>{node.subId || '—'}</span></div>
        <div>verifier    <span style={{ color: 'var(--ink)' }}>{node.verifier || '—'}</span></div>
        <div>official    <span style={{ color: 'var(--ink)' }}>{fmt(node.score)}</span></div>
        <div>decision    <span style={{ color: node.outcome === 'accept' ? 'var(--ok)' : node.outcome === 'reject' ? 'var(--bad)' : 'var(--ink-3)' }}>{node.outcome || 'pending'}</span></div>
      </div>
    );
  }

  function customVisualizationContent(view, node, speed) {
    const registry = window.AutoresearchVisualizations || {};
    const Comp = registry[view.type] || (view.component ? window[view.component] : null);
    if (!Comp) return null;
    return <Comp node={node} view={view} app={E} speed={speed} artifactUrl={artifactUrl} fmt={fmt} />;
  }

  function renderVisualization(view, node, speed) {
    const type = view.type;
    const title = view.label || labelText(type);
    const artifact = nodeArtifact(node);
    if (type === 'matmul_ir') {
      return <Section key={type} title={title} defaultOpen={false}>
        <pre className="well ir" style={{ marginBottom: 10 }}>{genIR(node)}</pre>
        <VerificationRecord node={node} />
      </Section>;
    }
    if (type === 'matmul_playback' && window.RunPlayback) {
      return <Section key={type} title={title} defaultOpen={true}>
        <window.RunPlayback node={node} speed={speed} />
      </Section>;
    }
    if (type === 'voxel_grid') {
      return <Section key={type} title={title} defaultOpen={true}>
        <VoxelBody artifact={artifact} />
      </Section>;
    }
    if (type === 'metrics') {
      const metrics = (artifact && artifact.metrics) || {};
      return <Section key={type} title={title} defaultOpen={true}>
        <MetricRows metrics={metrics} />
      </Section>;
    }
    if (type === 'media' || type === 'artifact_media' || type === 'rollout_media') {
      return <Section key={type} title={title} defaultOpen={true}>
        <ArtifactMedia artifact={artifact} />
      </Section>;
    }
    if (type === 'artifact_files') {
      return <Section key={type} title={title} defaultOpen={false}>
        <ArtifactFiles artifact={artifact} />
      </Section>;
    }
    if (type === 'artifact_bundle') {
      return <Section key={type} title={title} defaultOpen={true}>
        <VoxelBody artifact={artifact} />
        <MetricRows metrics={(artifact && artifact.metrics) || {}} />
        <ArtifactFiles artifact={artifact} />
      </Section>;
    }
    const custom = customVisualizationContent(view, node, speed);
    return custom ? <Section key={type} title={title} defaultOpen={view.defaultOpen !== false}>{custom}</Section> : null;
  }

  function InspectorPanel({ nodeId, T, speed, onClose, onSelect, branchSelection = [] }) {
    const node = E.nodes.find((n) => n.id === nodeId);
    const [controlText, setControlText] = useState('');
    const [injectMode, setInjectMode] = useState('branch');
    const [sourceId, setSourceId] = useState(branchSelection[0] || nodeId);
    const [targetId, setTargetId] = useState(branchSelection[1] || nodeId);
    const [controlStatus, setControlStatus] = useState('');
    useEffect(() => {
      const k = (e) => { if (e.key === 'Escape') onClose(); };
      window.addEventListener('keydown', k); return () => window.removeEventListener('keydown', k);
    }, [onClose]);
    useEffect(() => {
      if (branchSelection[0]) setSourceId(branchSelection[0]);
      if (branchSelection[1]) setTargetId(branchSelection[1]);
    }, [branchSelection.join('|')]);
    if (!node) return null;

    const st = E.fns.statusAt(node, T);
    const stNow = st === 'unborn' ? 'queued' : st;
    const parent = node.parent ? E.nodes.find((n) => n.id === node.parent) : null;
    const delta = (node.score != null && parent && parent.score != null) ? node.score - parent.score : null;
    const deltaGood = delta == null ? false : isMaximize() ? delta > 0 : delta < 0;
    const deltaBad = delta == null ? false : isMaximize() ? delta < 0 : delta > 0;
    const lineage = useMemo(() => { const arr = []; let cur = node; while (cur) { arr.unshift(cur); cur = cur.parent ? E.nodes.find((n) => n.id === cur.parent) : null; } return arr; }, [nodeId]);
    const children = E.nodes.filter((n) => n.parent === node.id);
    const selectable = E.nodes
      .filter((n) => n.outcome === 'accept')
      .sort((a, b) => {
        const as = a.score == null ? (isMaximize() ? -Infinity : Infinity) : a.score;
        const bs = b.score == null ? (isMaximize() ? -Infinity : Infinity) : b.score;
        return (isMaximize() ? bs - as : as - bs) || a.id.localeCompare(b.id);
      });
    const configuredViews = Array.isArray(E.meta.visualizations) ? E.meta.visualizations.filter((v) => v && v.type) : [];
    const views = configuredViews.length
      ? configuredViews
      : isMatmulDomain()
        ? [{ type: 'matmul_ir', label: 'Candidate IR & verification' }, { type: 'matmul_playback', label: 'Run playback' }]
        : [{ type: 'artifact_bundle', label: 'Artifacts' }];
    const visualizationSections = views.map((view) => renderVisualization(view, node, speed)).filter(Boolean);
    const apiPath = (path) => {
      const task = window.__AUTORESEARCH_JOURNAL || '';
      return path + (task ? (path.includes('?') ? '&' : '?') + 'journal=' + encodeURIComponent(task) : '');
    };
    const postControl = async (path, payload) => {
      setControlStatus('sending...');
      try {
        const res = await fetch(apiPath(path), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
        setControlStatus(data.hypothesisId ? 'queued ' + data.hypothesisId : 'updated');
        if (window.__AUTORESEARCH_APPLY_PAYLOAD) {
          const latest = await fetch(apiPath('/api/data?ts=' + Date.now()), { cache: 'no-store' }).then((r) => r.json());
          if (latest && latest.payload) window.__AUTORESEARCH_APPLY_PAYLOAD(latest.payload);
        }
      } catch (err) {
        setControlStatus('error: ' + err.message);
      }
    };

    return (
      <React.Fragment>
        <div className="insp-bar">
          <button className="insp-back" onClick={onClose}>
            <svg width={13} height={13} viewBox="0 0 16 16" fill="none">
              <path d="M10 4 L6 8 L10 12" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            team
          </button>
          <span className="insp-id">{node.id}</span>
          {statusPill(stNow)}
        </div>

        <div className="sb-scroll">
          {/* identity */}
          <div className="sb-section">
            <h3 className="insp-title">{node.title}</h3>
            <div className="insp-sub">
              <span className="insp-cand">{node.candidate}</span>
              <span className="insp-meta">{'gen ' + node.gen}</span>
              <span className="role-chip" style={{ '--role': roleCol(node.proposerRole) }}>
                <span className="glyph" />{ROLE_LABEL[node.proposerRole]}
              </span>
              <span className="insp-meta mono">{node.proposer}</span>
            </div>
          </div>

          {/* result */}
          <Section title="Result" defaultOpen={true}>
            {node.outcome === 'accept'
              ? <div>
                  <div className="insp-score-val" style={node.isFrontier ? { color: 'var(--accent)' } : null}>{fmt(node.score)}</div>
                  <div className="insp-score-row">
                    <span className="insp-meta">{E.meta.metric}</span>
                    {node.isFrontier ? <span className="frontier-tag">★ current frontier</span> : null}
                  </div>
                  <div className="prow"><span className="pk">Δ vs parent</span>
                    {delta != null ? <span className="pv" style={{ color: deltaGood ? 'var(--ok)' : deltaBad ? 'var(--bad)' : 'var(--ink-3)' }}>{(delta < 0 ? '▼ ' : delta > 0 ? '▲ ' : '') + fmt(Math.abs(delta))}</span> : <span className="pv">—</span>}
                  </div>
                  <div className="prow"><span className="pk">Semantic</span><span className="pv" style={{ color: 'var(--ok)' }}>{node.semantic}</span></div>
                </div>
              : node.outcome === 'reject'
                ? <div>
                    <div className="insp-score-val" style={{ color: 'var(--bad)' }}>rejected</div>
                    <div className="prow"><span className="pk">Semantic</span><span className="pv" style={{ color: node.semantic === 'invalid' ? 'var(--bad)' : 'var(--ink-1)' }}>{node.semantic}</span></div>
                  </div>
                : <div>
                    <div className="insp-score-val" style={{ color: 'var(--accent)', fontSize: 'var(--fs-lg)' }}>{stNow}</div>
                    <div className="insp-meta" style={{ marginTop: 6 }}>result not yet verified at this point in time</div>
                  </div>}
          </Section>

          <Section title="Branch controls" aside={node.halted ? 'halted' : branchSelection.length > 1 ? branchSelection.length + ' selected' : null} defaultOpen={true}>
            <div className="control-stack">
              <div className="control-row">
                <button className="btn" onClick={() => postControl('/api/control/halt', { nodeId: node.id, note: controlText })} disabled={node.halted}>Halt branch</button>
                <button className="btn" onClick={() => postControl('/api/control/unhalt', { nodeId: node.id })} disabled={!node.halted}>Resume</button>
              </div>
              <textarea className="control-text" value={controlText} onChange={(e) => setControlText(e.target.value)}
                placeholder="Inject information, transfer note, or branch halt reason" rows={4} />
              <div className="control-row">
                <select className="control-select" value={injectMode} onChange={(e) => setInjectMode(e.target.value)}>
                  <option value="branch">Parent under selected branch</option>
                  <option value="open">New open branch</option>
                </select>
                <button className="btn primary" onClick={() => postControl('/api/control/inject', {
                  nodeId: node.id,
                  mode: injectMode,
                  text: controlText,
                  priority: 80,
                })}>Inject</button>
              </div>
              <div className="control-pair">
                <label>source</label>
                <select className="control-select" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                  {selectable.map((n) => <option key={n.id} value={n.id}>{n.id + ' · ' + fmt(n.score)}</option>)}
                </select>
                <label>destination</label>
                <select className="control-select" value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                  {selectable.map((n) => <option key={n.id} value={n.id}>{n.id + ' · ' + fmt(n.score)}</option>)}
                </select>
                <button className="btn primary" onClick={() => postControl('/api/control/transfer', {
                  sourceId,
                  targetId,
                  note: controlText,
                  priority: 90,
                })}>Gene transfer</button>
              </div>
              <div className="insp-meta">
                Shift-click two nodes in the tree to fill source and destination.
                {controlStatus ? <span className="mono">{' · ' + controlStatus}</span> : null}
              </div>
            </div>
          </Section>

          {/* cost buckets */}
          {node.buckets ? <Section title="Cost buckets" defaultOpen={true}>
            <BucketBar buckets={node.buckets} />
          </Section> : null}

          {/* lineage */}
          <Section title="Lineage" aside={'gen ' + node.gen} defaultOpen={true}>
            <div className="lineage" style={{ marginLeft: -8, marginRight: -8 }}>
              {lineage.map((a) => (
                <div key={a.id} className={'lin-step' + (a.id === node.id ? ' cur' : '')} onClick={() => onSelect(a.id)}>
                  <span className="lin-dot" style={{ background: a.score != null ? `var(--fit-${a.fit})` : 'var(--line-strong)' }} />
                  <span className="lin-id">{a.id}</span>
                  <span className="lin-score">{a.score != null ? fmt(a.score) : '—'}</span>
                </div>
              ))}
            </div>
          </Section>

          {/* descendants */}
          {children.length ? <Section title="Descendants" aside={children.length} defaultOpen={false}>
            <div className="child-row">
              {children.map((ch) => (
                <button key={ch.id} className="child-chip" onClick={() => onSelect(ch.id)}>
                  <span className="lin-dot" style={{ background: ch.score != null ? `var(--fit-${ch.fit})` : 'var(--line-strong)' }} />
                  {ch.score != null ? fmt(ch.score) : ch.id}
                </button>
              ))}
            </div>
          </Section> : null}

          {/* timeline */}
          <Section title="Timeline" defaultOpen={false}>
            <div className="lifeline">
              {[['proposed', node.tProposed, node.proposer], ['claimed', node.tClaimed, node.impl], ['submitted', node.tSubmitted, node.impl], ['verified', node.tVerified, node.verifier]]
                .filter(([, tt]) => tt != null).map(([k, tt, who]) => (
                  <div key={k} className={'life-row' + (T >= tt ? ' done' : '')}>
                    <span className="life-k">{k}</span>
                    <span className="life-t">{mmss(tt)}</span>
                    <span className="life-who">{who || '—'}</span>
                  </div>
                ))}
            </div>
          </Section>

          {visualizationSections}
        </div>
      </React.Fragment>
    );
  }

  window.InspectorPanel = InspectorPanel;
})();
