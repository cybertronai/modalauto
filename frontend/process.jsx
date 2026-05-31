/* Autoresearch process view - live/replay view of multi-agent research flow. */
(function () {
  const { useEffect, useMemo, useRef, useState } = React;
  const ROLE_ORDER = ['topline_manager', 'meta_agent', 'insight_generator', 'creative_explorer', 'global_searcher', 'researcher', 'implementor', 'verifier'];
  const ROLE_LABEL = { topline_manager: 'manager', meta_agent: 'meta', insight_generator: 'insight', creative_explorer: 'explorer', global_searcher: 'searcher', researcher: 'researcher', implementor: 'implementor', verifier: 'verifier' };
  const ROLE_VAR = { topline_manager: '--role-manager', meta_agent: '--role-meta', insight_generator: '--role-insight', creative_explorer: '--role-explorer', global_searcher: '--role-searcher', researcher: '--role-researcher', implementor: '--role-implementor', verifier: '--role-verifier' };
  const roleCol = (r) => `var(${ROLE_VAR[r] || '--ink-3'})`;
  const fmt = (n) => n == null ? '-' : typeof n === 'number' ? n.toLocaleString(undefined, { maximumFractionDigits: Number.isInteger(n) ? 0 : 3 }) : String(n);
  const finiteTime = (t, fallback = 0) => (typeof t === 'number' && Number.isFinite(t) ? t : fallback);
  const mmss = (t) => {
    const safe = Math.max(0, finiteTime(t));
    const total = Math.round(safe);
    return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
  };
  const traceFor = (world, id) => id && world.traceById ? world.traceById[id] : null;
  const fmtDuration = (ms) => ms == null ? 'running' : ms < 1000 ? ms + ' ms' : (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + ' s';
  const activateKey = (fn) => (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      fn();
    }
  };

  function latestTrace(world, ids, T) {
    const traces = (Array.isArray(ids) ? ids : [])
      .map((id) => traceFor(world, id))
      .filter((tr) => tr && (tr.startedAt == null || tr.startedAt <= T));
    traces.sort((a, b) => (a.startedAt || 0) - (b.startedAt || 0));
    return traces[traces.length - 1] || null;
  }

  function TraceBadge({ trace, onTrace }) {
    if (!trace) return null;
    return (
      <button className={'trace-chip' + (trace.status === 'failed' ? ' failed' : '')}
        title={'View trace - ' + (trace.title || trace.id)}
        onClick={(e) => { e.stopPropagation(); onTrace(trace.id); }}>
        trace
      </button>
    );
  }

  function Logo() {
    return (
      <a className="logo" href="index.html" title="Back to tree">
        <svg viewBox="0 0 34 34" width={26} height={26} fill="none">
          <circle cx={6} cy={17} r={3} fill="var(--fit-1)" /><circle cx={17} cy={8} r={2.6} fill="var(--fit-3)" /><circle cx={17} cy={26} r={2.6} fill="var(--fit-2)" /><circle cx={28} cy={6} r={3.4} fill="var(--accent)" /><circle cx={28} cy={20} r={2.4} fill="var(--fit-4)" />
          <path d="M9 17 L14.6 9 M9 17 L14.6 25 M19.4 8 L25 6.5 M19.4 8 L26 19" stroke="var(--line-strong)" strokeWidth={1.2} />
        </svg>
        <span className="logo-name">Autoresearch</span>
      </a>
    );
  }

  function Stage({ id, title, roles, count, active, detail }) {
    return (
      <div className="proc-stage">
        <div className="proc-stage-num mono">{id}</div>
        <div className="proc-stage-main">
          <div className="proc-stage-title">{title}</div>
          <div className="proc-role-row">{roles.map((r) => <span key={r} className="role-chip" style={{ '--role': roleCol(r) }}><span className="glyph" />{ROLE_LABEL[r]}</span>)}</div>
          <div className="proc-stage-detail">{detail}</div>
        </div>
        <div className="proc-stage-stat"><span className="mono">{fmt(count)}</span><span>{active}</span></div>
      </div>
    );
  }

  function Architecture({ world, T }) {
    const visible = world.events.filter((e) => e.t <= T);
    const counts = {
      proposed: visible.filter((e) => e.kind === 'proposed').length,
      submitted: visible.filter((e) => e.kind === 'submitted').length,
      verified: visible.filter((e) => e.kind === 'verified').length,
      scale: visible.filter((e) => e.kind === 'scale').length,
    };
    return (
      <div className="proc-arch">
        <Stage id="01" title="Plan and scale" roles={['topline_manager', 'meta_agent']} count={counts.scale} active="manager cycles" detail="Reads backlog, frontier movement, stale leases, and role pressure; spawns or retires agents." />
        <Stage id="02" title="Generate research directions" roles={['researcher', 'insight_generator', 'creative_explorer', 'global_searcher']} count={counts.proposed} active="hypotheses" detail="Fetches papers, distills prior attempts, proposes operators, and queues experiments." />
        <Stage id="03" title="Implement candidates" roles={['implementor']} count={counts.submitted} active="submissions" detail="Claims hypotheses, writes candidate artifacts, records summaries, and hands off to verification." />
        <Stage id="04" title="Verify and update frontier" roles={['verifier']} count={counts.verified} active="verifications" detail="Runs semantic checks, scores artifacts, accepts or rejects submissions, and updates the live tree." />
      </div>
    );
  }

  function RolePool({ world, T, onTrace }) {
    const activity = world.fns.agentActivity(T);
    return (
      <div className="proc-card">
        <div className="proc-card-head"><h3>Agent pools</h3><span className="mono">{world.agents.length} agents</span></div>
        <div className="proc-pool-grid">
          {ROLE_ORDER.map((role) => {
            const agents = world.agents.filter((a) => a.role === role);
            if (!agents.length) return null;
            const busy = agents.filter((a) => activity[a.id] && activity[a.id].status === 'working').length;
            return (
              <div key={role} className="proc-pool">
                <div className="proc-pool-head"><span className="proc-pool-dot" style={{ background: roleCol(role) }} /><span>{ROLE_LABEL[role]}</span><span className="mono">{busy + '/' + agents.length}</span></div>
                {agents.slice(0, 5).map((a) => {
                  const act = activity[a.id] || {};
                  const trace = latestTrace(world, a.traceIds, T);
                  return (
                    <div key={a.id} className={'proc-agent mono' + (trace ? ' clickable' : '')} role={trace ? 'button' : undefined} tabIndex={trace ? 0 : undefined} onClick={trace ? () => onTrace(trace.id) : undefined} onKeyDown={trace ? activateKey(() => onTrace(trace.id)) : undefined}>
                      <span className="proc-agent-id">{a.id}</span>
                      <span>{act.status || a.status}</span>
                      <TraceBadge trace={trace} onTrace={onTrace} />
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function WorkQueues({ world, T, onTrace }) {
    const items = (world.hypotheses || []).filter((h) => h.createdAt <= T);
    const queued = items.filter((h) => h.status === 'queued').length;
    const claimed = items.filter((h) => h.status === 'claimed').length;
    const submitted = items.filter((h) => h.hasSubmission && !h.hasVerification).length;
    const verified = items.filter((h) => h.hasVerification).length;
    const active = items.filter((h) => !h.inTree).slice(-10).reverse();
    return (
      <div className="proc-card">
        <div className="proc-card-head"><h3>Work queues</h3><span className="mono">{items.length} hypotheses</span></div>
        <div className="proc-queue-bars">{[['queued', queued], ['claimed', claimed], ['submitted', submitted], ['verified', verified]].map(([k, v]) => <div key={k} className="proc-q"><span>{k}</span><b className="mono">{v}</b></div>)}</div>
        <div className="proc-work-list">{active.length ? active.map((h) => {
          const trace = latestTrace(world, h.traceIds, T);
          return (
            <div key={h.id} className={'proc-work' + (trace ? ' clickable' : '')} role={trace ? 'button' : undefined} tabIndex={trace ? 0 : undefined} onClick={trace ? () => onTrace(trace.id) : undefined} onKeyDown={trace ? activateKey(() => onTrace(trace.id)) : undefined}>
              <span className="mono">{h.id}</span><span>{h.title}</span><b>{h.status}</b><TraceBadge trace={trace} onTrace={onTrace} />
            </div>
          );
        }) : <div className="proc-empty">No active work items at this time.</div>}</div>
      </div>
    );
  }

  function EventStream({ world, T, onTrace }) {
    const events = world.events.filter((e) => e.t <= T).slice(-42).reverse();
    return (
      <div className="proc-card proc-events">
        <div className="proc-card-head"><h3>Execution log</h3><span className="mono">{events.length} visible</span></div>
        <div className="proc-event-list">
          {events.map((e, i) => {
            const clickable = e.traceId && traceFor(world, e.traceId);
            const Tag = clickable ? 'button' : 'div';
            return <Tag key={e.t + ':' + i + ':' + e.kind} className={'proc-event' + (clickable ? ' clickable' : '')} onClick={clickable ? () => onTrace(e.traceId) : null}><span className="proc-event-dot" style={{ background: roleCol(e.role) }} /><span className="mono proc-event-time">{mmss(e.t)}</span><span className="proc-event-kind">{e.kind}</span><span className="proc-event-body">{e.agent ? e.agent + ' - ' : ''}{e.text || e.decision || e.nodeId || ''}</span></Tag>;
          })}
        </div>
      </div>
    );
  }

  function TraceDetail({ world, traceId, onClose, detailRef }) {
    const trace = traceFor(world, traceId);
    if (!trace) return (
      <div className="proc-card proc-trace-card" ref={detailRef}>
        <div className="proc-card-head"><h3>Trace</h3><button className="btn" onClick={onClose}>Close</button></div>
        <div className="proc-empty">Select an agent, work item, or trace event.</div>
      </div>
    );
    const spans = Array.isArray(trace.spans) ? trace.spans : [];
    const maxMs = Math.max(1, ...spans.map((s) => s.durationMs || 0));
    return (
      <div className="proc-card proc-trace-card" ref={detailRef}>
        <div className="proc-card-head">
          <h3>Trace</h3>
          <div className="proc-card-actions">
            {trace.workshopUrl ? <a className="btn" href={trace.workshopUrl} target="_blank" rel="noreferrer">Workshop</a> : null}
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
        <div className="proc-trace-body">
          <div className="proc-trace-title">{trace.title || trace.id}</div>
          <div className="proc-trace-meta">
            <span className={'trace-status ' + (trace.status || 'ok')}>{trace.status || 'trace'}</span>
            <span className="mono">{trace.role || 'agent'}</span>
            <span className="mono">{trace.agentId || '-'}</span>
            <span className="mono">{fmtDuration(trace.durationMs)}</span>
          </div>
          <div className="proc-trace-summary">
            <div><span>kind</span><b>{trace.kind}</b></div>
            <div><span>item</span><b>{trace.itemId || '-'}</b></div>
            <div><span>run</span><b>{trace.runId || '-'}</b></div>
          </div>
          <div className="agent-trace-list proc-trace-spans">
            {spans.length ? spans.map((span) => (
              <div key={span.id} className={'agent-trace-row ' + (span.status || 'ok')}>
                <div className="agent-trace-top">
                  <span className="agent-trace-name">{span.name}</span>
                  <span className="agent-trace-kind">{span.kind}</span>
                  <span className="agent-trace-ms">{fmtDuration(span.durationMs)}</span>
                </div>
                <div className="agent-trace-bar"><span style={{ width: Math.max(2, ((span.durationMs || 0) / maxMs) * 100) + '%' }} /></div>
                {span.error ? <div className="agent-trace-error">{span.error}</div> : null}
              </div>
            )) : <div className="proc-empty">No child spans recorded.</div>}
          </div>
        </div>
      </div>
    );
  }

  function ProcessScrubber({ world, T, setT, playing, setPlaying }) {
    const ref = useRef(null);
    const live = T >= world.meta.tNow - 0.5;
    const tMax = Math.max(1, finiteTime(world.meta.tMax, 1));
    const pct = Math.max(0, Math.min(100, (finiteTime(T) / tMax) * 100));
    const seek = (clientX) => {
      const r = ref.current.getBoundingClientRect();
      setT(Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * tMax);
      setPlaying(false);
    };
    return (
      <div className="proc-scrub">
        <button className="btn primary play-btn" onClick={() => setPlaying((p) => !p)}>{playing ? 'Pause' : live ? 'Live' : 'Replay'}</button>
        <div className="proc-track" ref={ref} onPointerDown={(e) => seek(e.clientX)}><div className="proc-track-fill" style={{ width: pct + '%' }} /><div className="proc-track-head" style={{ left: pct + '%' }} /></div>
        <span className="mono proc-time">{mmss(T)} / {mmss(world.meta.tMax)}</span>
        <button className="btn jump-btn" onClick={() => { setT(world.meta.tNow); setPlaying(false); }}>Jump to now</button>
      </div>
    );
  }

  function App() {
    const [world, setWorld] = useState(window.APP);
    const [T, setT] = useState(() => (window.APP && window.APP.meta.tNow) || 0);
    const [playing, setPlaying] = useState(false);
    const [selectedTrace, setSelectedTrace] = useState(null);
    const traceDetailRef = useRef(null);
    useEffect(() => {
      if (!world || !playing) return;
      const step = Math.max(0.05, world.meta.tMax / 700);
      const id = setInterval(() => setT((cur) => cur >= world.meta.tMax ? world.meta.tMax : Math.min(world.meta.tMax, cur + step)), 33);
      return () => clearInterval(id);
    }, [world, playing]);
    useEffect(() => {
      if (!selectedTrace) return;
      const id = setTimeout(() => {
        if (traceDetailRef.current) {
          traceDetailRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 0);
      return () => clearTimeout(id);
    }, [selectedTrace]);
    useEffect(() => {
      let stopped = false;
      async function load() {
        try {
          const res = await fetch('/api/data?ts=' + Date.now(), { cache: 'no-store' });
          const data = await res.json();
          if (!stopped && data && data.payload && window.appWorld) {
            const next = window.appWorld(data.payload);
            setWorld(next);
            setT((cur) => cur >= ((world && world.meta.tNow) || 0) - 1 ? next.meta.tNow : Math.min(cur, next.meta.tMax));
            setSelectedTrace((id) => id && next.traceById && next.traceById[id] ? id : null);
          }
        } catch (_) {}
      }
      load();
      let es = null;
      if (window.EventSource) {
        es = new EventSource('/api/events');
        es.addEventListener('change', load);
      }
      const poll = setInterval(load, 5000);
      return () => { stopped = true; clearInterval(poll); if (es) es.close(); };
    }, []);
    if (!world) return <div className="app"><header className="top"><Logo /></header></div>;
    return (
      <div className="app proc-app">
        <header className="top">
          <div className="top-left">
            <Logo />
            <nav className="nav-tabs"><a className="nav-tab" href="index.html">Tree</a><a className="nav-tab" href="compare.html">Compare</a><a className="nav-tab active" href="process.html">Process</a></nav>
            <div className="prob"><span className="prob-name">Research generation process</span><span className="prob-sub mono">{world.meta.problem + ' - ' + world.meta.totalNodes + ' tree nodes'}</span></div>
          </div>
          <div className="stat-row"><div className="stat"><span className="k">Best {world.meta.metric}</span><span className="v">{fmt(world.meta.best)}</span></div><div className="stat"><span className="k">Events</span><span className="v">{world.events.length}</span></div></div>
        </header>
        <main className="proc-main">
          <Architecture world={world} T={T} />
          <div className="proc-grid">
            <RolePool world={world} T={T} onTrace={setSelectedTrace} />
            <WorkQueues world={world} T={T} onTrace={setSelectedTrace} />
            <EventStream world={world} T={T} onTrace={setSelectedTrace} />
            {selectedTrace ? <TraceDetail world={world} traceId={selectedTrace} onClose={() => setSelectedTrace(null)} detailRef={traceDetailRef} /> : null}
          </div>
        </main>
        <footer className="bottom"><ProcessScrubber world={world} T={T} setT={setT} playing={playing} setPlaying={setPlaying} /></footer>
      </div>
    );
  }

  async function boot() {
    if (!window.APP) {
      try {
        const res = await fetch('/api/data?ts=' + Date.now(), { cache: 'no-store' });
        const data = await res.json();
        if (data && data.payload && window.appWorld) window.APP = window.appWorld(data.payload);
      } catch (_) {}
    }
    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  }
  boot();
})();
