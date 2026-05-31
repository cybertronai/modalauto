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

  function RolePool({ world, T }) {
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
                  return <div key={a.id} className="proc-agent mono">{a.id}<span>{act.status || a.status}</span></div>;
                })}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  function WorkQueues({ world, T }) {
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
        <div className="proc-work-list">{active.length ? active.map((h) => <div key={h.id} className="proc-work"><span className="mono">{h.id}</span><span>{h.title}</span><b>{h.status}</b></div>) : <div className="proc-empty">No active work items at this time.</div>}</div>
      </div>
    );
  }

  function EventStream({ world, T }) {
    const events = world.events.filter((e) => e.t <= T).slice(-42).reverse();
    return (
      <div className="proc-card proc-events">
        <div className="proc-card-head"><h3>Execution log</h3><span className="mono">{events.length} visible</span></div>
        <div className="proc-event-list">
          {events.map((e, i) => <div key={e.t + ':' + i + ':' + e.kind} className="proc-event"><span className="proc-event-dot" style={{ background: roleCol(e.role) }} /><span className="mono proc-event-time">{mmss(e.t)}</span><span className="proc-event-kind">{e.kind}</span><span className="proc-event-body">{e.agent ? e.agent + ' - ' : ''}{e.text || e.decision || e.nodeId || ''}</span></div>)}
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
    const worldRef = useRef(world);
    const [T, setT] = useState(() => (window.APP && window.APP.meta.tNow) || 0);
    const [playing, setPlaying] = useState(false);
    useEffect(() => {
      worldRef.current = world;
    }, [world]);
    useEffect(() => {
      if (!world || !playing) return;
      const step = Math.max(0.05, world.meta.tMax / 700);
      const id = setInterval(() => setT((cur) => cur >= world.meta.tMax ? world.meta.tMax : Math.min(world.meta.tMax, cur + step)), 33);
      return () => clearInterval(id);
    }, [world, playing]);
    useEffect(() => {
      window.__AUTORESEARCH_APPLY_PAYLOAD = (payload) => {
        if (!payload || !window.appWorld) return;
        const previous = worldRef.current;
        const next = window.appWorld(payload);
        window.APP = next;
        worldRef.current = next;
        setWorld(next);
        setT((cur) => {
          const wasLive = !previous || cur >= previous.meta.tNow - 1;
          return wasLive ? next.meta.tNow : Math.min(cur, next.meta.tMax);
        });
      };
      if (window.__AUTORESEARCH_PENDING_PAYLOAD) {
        const pending = window.__AUTORESEARCH_PENDING_PAYLOAD;
        delete window.__AUTORESEARCH_PENDING_PAYLOAD;
        delete window.__AUTORESEARCH_PENDING_DATA;
        window.__AUTORESEARCH_APPLY_PAYLOAD(pending);
      }
      return () => { delete window.__AUTORESEARCH_APPLY_PAYLOAD; };
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
          <div className="proc-grid"><RolePool world={world} T={T} /><WorkQueues world={world} T={T} /><EventStream world={world} T={T} /></div>
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
