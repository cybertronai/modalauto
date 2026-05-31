/* Autoresearch app — header + evotree + live agent rail + inspector + time scrubber. */
(function () {
  const { useState, useEffect, useRef, useMemo, useCallback } = React;
  let E = null;

  const ACCENTS = {
    blue:  { '--accent': 'oklch(0.55 0.10 245)', '--accent-deep': 'oklch(0.48 0.10 245)', '--accent-soft': 'oklch(0.55 0.10 245 / 0.10)', '--accent-glow': 'oklch(0.55 0.10 245 / 0.14)' },
    teal:  { '--accent': 'oklch(0.52 0.085 195)', '--accent-deep': 'oklch(0.45 0.085 195)', '--accent-soft': 'oklch(0.52 0.085 195 / 0.10)', '--accent-glow': 'oklch(0.52 0.085 195 / 0.14)' },
    plum:  { '--accent': 'oklch(0.52 0.09 310)', '--accent-deep': 'oklch(0.45 0.09 310)', '--accent-soft': 'oklch(0.52 0.09 310 / 0.10)', '--accent-glow': 'oklch(0.52 0.09 310 / 0.14)' },
  };

  const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
    "theme": "light",
    "accent": "blue",
    "density": "regular",
    "speed": 1,
    "scoreLabels": true,
    "dimOffLineage": true,
    "showFeed": true
  }/*EDITMODE-END*/;

  const fmt = (n) => {
    if (n == null) return '—';
    if (typeof n !== 'number') return String(n);
    if (!Number.isFinite(n)) return String(n);
    const abs = Math.abs(n);
    const maximumFractionDigits = Number.isInteger(n) ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
    return n.toLocaleString(undefined, { maximumFractionDigits });
  };
  const finiteTime = (t, fallback = 0) => (typeof t === 'number' && Number.isFinite(t) ? t : fallback);
  const mmss = (t) => {
    const safe = Math.max(0, finiteTime(t));
    const total = Math.round(safe);
    return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
  };
  const directionLabel = () => String((E && E.meta && E.meta.direction) || 'minimize').toLowerCase() === 'maximize' ? 'maximize' : 'minimize';
  const hoverPreviewKey = (world) => {
    const meta = (world && world.meta) || {};
    return 'autoresearch.hoverPreviewRuns:' + (meta.source || meta.sourceDb || meta.problem || 'default');
  };
  const readHoverPreviewIds = (key) => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(key) || '[]');
      return Array.isArray(parsed) ? parsed.filter((id) => typeof id === 'string') : [];
    } catch (_) {
      return [];
    }
  };

  function Logo() {
    return (
      <div className="logo">
        <svg viewBox="0 0 34 34" width={26} height={26} fill="none">
          <circle cx={6} cy={17} r={3} fill="var(--fit-1)" />
          <circle cx={17} cy={8} r={2.6} fill="var(--fit-3)" />
          <circle cx={17} cy={26} r={2.6} fill="var(--fit-2)" />
          <circle cx={28} cy={6} r={3.4} fill="var(--accent)" />
          <circle cx={28} cy={20} r={2.4} fill="var(--fit-4)" />
          <circle cx={28} cy={28} r={2.2} fill="var(--fit-3)" />
          <path d="M9 17 L14.6 9 M9 17 L14.6 25 M19.4 8 L25 6.5 M19.4 8 L26 19 M19.4 26 L26 27.5" stroke="var(--line-strong)" strokeWidth={1.2} />
        </svg>
        <span className="logo-name">Autoresearch</span>
      </div>
    );
  }

  function StatTiles({ T }) {
    const born = E.fns.bornCount(T);
    const best = E.fns.frontierAt(T);
    const act = E.fns.agentActivity(T);
    const liveAgents = E.agents.filter((a) => act[a.id] && act[a.id].alive).length;
    const working = E.agents.filter((a) => act[a.id] && act[a.id].alive && act[a.id].status === 'working').length;
    return (
      <div className="stat-row">
        <div className="stat"><span className="k">{'Best ' + (E.meta.metric || 'score')}</span>
          <span className="v" style={{ color: 'var(--fit-6)' }}>{fmt(best)}</span></div>
        <div className="stat"><span className="k">Experiments</span>
          <span className="v">{born}</span></div>
        <div className="stat"><span className="k">Live agents</span>
          <span className="v">{liveAgents}<span className="stat-frac mono">{working + ' busy'}</span></span></div>
      </div>
    );
  }

  function ChangelogBadge() {
    const [info, setInfo] = useState(null);
    useEffect(() => {
      let stopped = false;
      async function load() {
        try {
          const res = await fetch('/api/changelog?ts=' + Date.now(), { cache: 'no-store' });
          if (!res.ok) return;
          const next = await res.json();
          if (!stopped) setInfo(next);
        } catch (_) {}
      }
      load();
      const id = setInterval(load, 3000);
      return () => { stopped = true; clearInterval(id); };
    }, [E ? E.series : null, E ? E.meta.baseline : null, E ? E.meta.best : null, E ? E.meta.tMax : null]);
    if (!info || !info.frames) return null;
    const last = info.frames[info.frames.length - 1];
    const counts = last && last.counts ? last.counts : {};
    return (
      <div className="run-badge" title={info.changelog || info.journal || 'live database'}>
        <span className="live-tag">● LIVE DB</span>
        <span className="mono">{(counts.hypotheses || E.meta.totalNodes) + ' hyp'}</span>
      </div>
    );
  }

  function Scrubber({ T, setT, playing, setPlaying, speed, setSpeed }) {
    const trackRef = useRef(null);
    const dragging = useRef(false);
    const empty = E.meta.totalNodes === 0;
    const live = T >= E.meta.tNow - 1;
    if (empty) {
      return (
        <div className="scrubber scrubber-empty">
          <button className="btn primary play-btn" disabled={true}>
            ●<span>Live</span></button>
          <div className="scrub-track-wrap">
            <div className="scrub-time mono">
              <span>Step 0 (00:00)</span>
              <span className="live-tag">● LIVE</span>
            </div>
            <div className="scrub-empty-track">waiting for first run</div>
          </div>
          <button className="btn jump-btn" disabled={true}>Jump to now</button>
        </div>
      );
    }

    const sparkPath = useMemo(() => {
      const s = E.series;
      const maximize = directionLabel() === 'maximize';
      const values = [E.meta.baseline, E.meta.target].concat(s.map((p) => p.best)).filter((v) => typeof v === 'number' && Number.isFinite(v));
      const lo = values.length ? Math.min(...values) : 0;
      const hi = values.length ? Math.max(...values) : 1;
      const span = Math.max(1e-9, hi - lo);
      return s.map((p, i) => {
        const x = (p.t / E.meta.tMax) * 100;
        const better = p.best == null ? 0 : maximize ? (p.best - lo) / span : (hi - p.best) / span;
        const y = p.best == null ? 92 : 92 - Math.max(0, Math.min(1, better)) * 84;
        return (i === 0 ? 'M' : 'L') + x.toFixed(2) + ' ' + y.toFixed(2);
      }).join(' ');
    }, [E.series, E.meta.baseline, E.meta.target, E.meta.direction, E.meta.tMax]);
    const sparkFill = sparkPath + ` L 100 100 L 0 100 Z`;

    const setFromX = (clientX) => {
      const r = trackRef.current.getBoundingClientRect();
      const f = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      setT(f * E.meta.tMax); setPlaying(false);
    };
    const down = (e) => { dragging.current = true; setFromX(e.clientX); };
    const move = (e) => { if (dragging.current) setFromX(e.clientX); };
    const up = () => { dragging.current = false; };
    useEffect(() => { window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
      return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); }; }, []);

    const pct = (T / E.meta.tMax) * 100;
    const bornNow = E.fns.bornCount(T);
    const playLabel = playing ? 'Pause' : live ? 'Live' : 'Replay';
    const playIcon = playing ? '❚❚' : live ? '●' : '▶';

    return (
      <div className="scrubber">
        <button className="btn primary play-btn" onClick={() => setPlaying((p) => !p)}>
          {playIcon}<span>{playLabel}</span></button>
        <div className="scrub-track-wrap">
          <div className="scrub-time mono">
            <span>{'Step ' + bornNow + ' (' + mmss(T) + ')'}</span>
            {live ? <span className="live-tag">● LIVE</span> : <span className="mono scrub-of">{' of ' + E.fns.bornCount(E.meta.tMax) + ' (' + mmss(E.meta.tMax) + ')'}</span>}
          </div>
          <div className="scrub-track" ref={trackRef} onPointerDown={down}>
            <svg className="spark" viewBox="0 0 100 100" preserveAspectRatio="none">
              <path d={sparkFill} fill="var(--accent-soft)" />
              <path d={sparkPath} fill="none" stroke="var(--accent)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
            </svg>
            <div className="scrub-done" style={{ width: pct + '%' }} />
            <div className="scrub-head" style={{ left: pct + '%' }} />
          </div>
        </div>
        <div className="scrub-right">
          <div className="speed-seg">
            {[1, 2, 4].map((s) => <button key={s} className={'speed-btn' + (speed === s ? ' on' : '')} onClick={() => setSpeed(s)}>{s + '×'}</button>)}
          </div>
          <button className={'btn jump-btn' + (live ? ' on' : '')} onClick={() => { setT(E.meta.tNow); setPlaying(false); }}>Jump to now</button>
        </div>
      </div>
    );
  }

  function App() {
    const [world, setWorld] = useState(window.APP);
    const worldRef = useRef(world);
    E = world;
    const EvoTree = window.EvoTree;
    const InspectorPanel = window.InspectorPanel;
    const TeamPanel = window.TeamPanel;
    const HypothesesPanel = window.HypothesesPanel;
    const TracesPanel = window.TracesPanel;
    const ActivityPanel = window.ActivityPanel;
    const TracePanel = window.TracePanel;
    const TweaksPanel = window.TweaksPanel;
    const TweakSection = window.TweakSection;
    const TweakRadio = window.TweakRadio;
    const TweakColor = window.TweakColor;
    const TweakToggle = window.TweakToggle;
    const [t, setTweak] = window.useTweaks(TWEAK_DEFAULTS);
    const [T, setT] = useState(E ? E.meta.tNow : 0);
    const [playing, setPlaying] = useState(false);
    const [speed, setSpeed] = useState(t.speed || 1);
    const [selected, setSelected] = useState(null);
    const [selectedTrace, setSelectedTrace] = useState(null);
    const [branchSelection, setBranchSelection] = useState([]);
    const previewStoreKey = useMemo(() => hoverPreviewKey(world), [world && world.meta && world.meta.source, world && world.meta && world.meta.sourceDb, world && world.meta && world.meta.problem]);
    const [hoverPreviewIds, setHoverPreviewIds] = useState(() => readHoverPreviewIds(hoverPreviewKey(E)));

    useEffect(() => {
      worldRef.current = world;
      E = world;
    }, [world]);

    useEffect(() => {
      window.__AUTORESEARCH_APPLY_PAYLOAD = (payload) => {
        if (!payload || !window.appWorld) return;
        const previous = worldRef.current;
        const next = window.appWorld(payload);
        window.APP = next;
        worldRef.current = next;
        setWorld(next);
        window.dispatchEvent(new CustomEvent('autoresearch-world', { detail: next }));
        setT((cur) => {
          const wasLive = !previous || cur >= previous.meta.tNow - 1;
          return wasLive ? next.meta.tNow : Math.min(cur, next.meta.tMax);
        });
        setSelected((id) => id && next.nodes.some((n) => n.id === id) ? id : null);
        setSelectedTrace((id) => id && next.traceById && next.traceById[id] ? id : null);
        setBranchSelection((ids) => ids.filter((id) => next.nodes.some((n) => n.id === id)).slice(-2));
      };
      if (window.__AUTORESEARCH_PENDING_PAYLOAD) {
        const pending = window.__AUTORESEARCH_PENDING_PAYLOAD;
        delete window.__AUTORESEARCH_PENDING_PAYLOAD;
        window.__AUTORESEARCH_APPLY_PAYLOAD(pending);
      }
      return () => { delete window.__AUTORESEARCH_APPLY_PAYLOAD; };
    }, []);

    useEffect(() => {
      setHoverPreviewIds(readHoverPreviewIds(previewStoreKey));
    }, [previewStoreKey]);

    useEffect(() => {
      const existing = new Set((world.nodes || []).map((n) => n.id));
      setHoverPreviewIds((ids) => {
        const next = ids.filter((id) => existing.has(id));
        return next.length === ids.length ? ids : next;
      });
    }, [world]);

    useEffect(() => {
      try {
        window.localStorage.setItem(previewStoreKey, JSON.stringify(hoverPreviewIds));
      } catch (_) {}
    }, [previewStoreKey, hoverPreviewIds]);

    useEffect(() => {
      if (!window.EventSource) return;
      let stopped = false;
      let inFlight = false;
      async function loadLatest() {
        if (inFlight) return;
        inFlight = true;
        try {
          const res = await fetch('/api/data?ts=' + Date.now(), { cache: 'no-store' });
          if (!res.ok) return;
          const data = await res.json();
          if (!stopped && data && data.payload) {
            window.__AUTORESEARCH_APPLY_PAYLOAD(data.payload);
          }
        } catch (_) {
        } finally {
          inFlight = false;
        }
      }
      const events = new EventSource('/api/events');
      events.addEventListener('change', loadLatest);
      events.onerror = () => {};
      return () => {
        stopped = true;
        events.close();
      };
    }, []);

    // theme + accent
    useEffect(() => { if (t.theme === 'dark') document.documentElement.dataset.theme = 'dark'; else document.documentElement.removeAttribute('data-theme'); }, [t.theme]);
    useEffect(() => { const a = ACCENTS[t.accent] || ACCENTS.blue; Object.entries(a).forEach(([k, v]) => document.documentElement.style.setProperty(k, v)); }, [t.accent]);
    useEffect(() => { document.documentElement.dataset.density = t.density; }, [t.density]);
    useEffect(() => { setSpeed(t.speed || 1); }, [t.speed]);

    // playback loop
    useEffect(() => {
      if (!playing || !E) return;
      const step = E.meta.tMax / 900;
      const iv = setInterval(() => {
        setT((cur) => { const nx = cur + step * speed; if (nx >= E.meta.tMax) { setPlaying(false); return E.meta.tMax; } return nx; });
      }, 33);
      return () => clearInterval(iv);
    }, [playing, speed]);

    const onSelect = useCallback((id, opts = {}) => {
      setSelected(id);
      setSelectedTrace(null);
      setBranchSelection((prev) => {
        if (!opts.shift) return [id];
        const next = prev.filter((x) => x !== id).concat(id);
        return next.slice(-2);
      });
    }, []);
    const onTrace = useCallback((id) => {
      setSelected(null);
      setSelectedTrace(id);
    }, []);
    const onToggleHoverPreview = useCallback((id) => {
      setHoverPreviewIds((ids) => {
        if (ids.includes(id)) return ids.filter((x) => x !== id);
        return ids.concat(id);
      });
    }, []);

    if (!world) {
      return (
        <div className="app app-empty">
          <header className="top">
            <div className="top-left">
              <Logo />
              <div className="prob">
                <span className="prob-name">No live autoresearch data</span>
                <span className="prob-sub mono">start the Autoresearch server with a journal DB</span>
              </div>
            </div>
          </header>
        </div>
      );
    }

    return (
      <div className="app">
        <header className="top">
          <div className="top-left">
            <Logo />
            <nav className="nav-tabs">
              <a className="nav-tab active" href="index.html">Tree</a>
              <a className="nav-tab" href="compare.html">Compare</a>
              <a className="nav-tab" href="process.html">Process</a>
            </nav>
            <div className="prob">
              <span className="prob-name">{E.meta.problem}</span>
              <span className="prob-sub mono">{directionLabel() + ' ' + E.meta.metric + ' · baseline ' + fmt(E.meta.baseline)}</span>
            </div>
            <ChangelogBadge />
          </div>
          <StatTiles T={T} />
        </header>

        <div className="body">
          <main className="canvas">
            <EvoTree
              T={T}
              selected={selected}
              onSelect={onSelect}
              density={t.density}
              scoreLabels={t.scoreLabels}
              dimOffLineage={t.dimOffLineage}
              hoverPreviewIds={hoverPreviewIds}
              onToggleHoverPreview={onToggleHoverPreview}
            />
          </main>
          <aside className="sidebar">
            {selectedTrace
            ? <TracePanel traceId={selectedTrace} onClose={() => setSelectedTrace(null)} onSelect={onSelect} />
            : selected
            ? <InspectorPanel
                nodeId={selected}
                T={T}
                speed={speed}
                onClose={() => setSelected(null)}
                onSelect={onSelect}
                onTrace={onTrace}
                branchSelection={branchSelection}
                hoverPreviewIds={hoverPreviewIds}
                onToggleHoverPreview={onToggleHoverPreview}
              />
              : <div className="sb-split">
                  <TeamPanel T={T} onSelect={onSelect} onTrace={onTrace} />
                  <HypothesesPanel T={T} onSelect={onSelect} />
                  <TracesPanel T={T} onTrace={onTrace} />
                  {t.showFeed ? <ActivityPanel T={T} onSelect={onSelect} onTrace={onTrace} /> : null}
                </div>}
          </aside>
        </div>

        <footer className="bottom">
          <Scrubber T={T} setT={setT} playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed} />
        </footer>

        {/* Tweaks */}
        <TweaksPanel>
          <TweakSection label="Appearance" />
          <TweakRadio label="Theme" value={t.theme} options={['light', 'dark']} onChange={(v) => setTweak('theme', v)} />
          <TweakColor label="Accent" value={t.accent} options={['blue', 'teal', 'plum'].map((k) => ACCENTS[k]['--accent'])} onChange={(v) => {
            const key = Object.keys(ACCENTS).find((k) => ACCENTS[k]['--accent'] === v) || 'blue'; setTweak('accent', key); }} />
          <TweakRadio label="Density" value={t.density} options={['compact', 'regular', 'comfy']} onChange={(v) => setTweak('density', v)} />
          <TweakSection label="Tree" />
          <TweakToggle label="Score labels" value={t.scoreLabels} onChange={(v) => setTweak('scoreLabels', v)} />
          <TweakToggle label="Dim off-lineage" value={t.dimOffLineage} onChange={(v) => setTweak('dimOffLineage', v)} />
          <TweakSection label="Playback" />
          <TweakRadio label="Default speed" value={String(t.speed)} options={['1', '2', '4']} onChange={(v) => setTweak('speed', +v)} />
          <TweakToggle label="Show message feed" value={t.showFeed} onChange={(v) => setTweak('showFeed', v)} />
        </TweaksPanel>
      </div>
    );
  }

  function boot() {
    ReactDOM.createRoot(document.getElementById('root')).render(<App />);
  }

  async function loadLiveData() {
    try {
      const base = window.FRONTEND_API_URL || '';
      const endpoint = base ? base.replace(/\/$/, '') + '/api/data' : '/api/data';
      const res = await fetch(endpoint + '?ts=' + Date.now(), { cache: 'no-store' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      if (data && data.payload && window.appWorld) {
        window.APP = window.appWorld(data.payload);
      }
    } catch (_) {
      // Keep the synchronous real-data.js payload when the polling endpoint is unavailable.
    }
  }

  loadLiveData().then(boot);
})();
