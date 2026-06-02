/* Autoresearch app — header + evotree + live agent rail + inspector + time scrubber. */
(function () {
  const { useState, useEffect, useRef, useMemo, useCallback } = React;
  const UI = window.AutoresearchUI;
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

  const fmt = UI.fmt;
  const finiteTime = (t, fallback = 0) => (typeof t === 'number' && Number.isFinite(t) ? t : fallback);
  const mmss = UI.mmss;
  const directionLabel = () => String((E && E.meta && E.meta.direction) || 'minimize').toLowerCase() === 'maximize' ? 'maximize' : 'minimize';
  const initialTask = UI.currentTask;
  const initialNode = () => UI.queryParam('node');
  const apiUrl = UI.apiUrl;

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

  function ChangelogBadge({ task }) {
    const [info, setInfo] = useState(null);
    useEffect(() => {
      let stopped = false;
      async function load() {
        try {
          const res = await fetch(apiUrl('/api/changelog', task, { ts: Date.now() }), { cache: 'no-store' });
          if (!res.ok) return;
          const next = await res.json();
          if (!stopped) setInfo(next);
        } catch (_) {}
      }
      load();
      const id = setInterval(load, 3000);
      return () => { stopped = true; clearInterval(id); };
    }, [task, E ? E.series : null, E ? E.meta.baseline : null, E ? E.meta.best : null, E ? E.meta.tMax : null]);
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

  function TaskSelect({ tasks, value, onChange }) {
    if (!tasks.length) return null;
    return (
      <label className="task-select" title="Select autoresearch journal">
        <span>Journal</span>
        <select value={value || ''} onChange={(e) => onChange(e.target.value)}>
          {tasks.map((task) => <option key={task.id} value={task.id}>{task.label}</option>)}
        </select>
      </label>
    );
  }

  function Scrubber({ T, setT, playing, setPlaying, speed, setSpeed, onStopRun, stopStatus }) {
    const trackRef = useRef(null);
    const dragging = useRef(false);
    const eventCount = E.fns.eventCount ? E.fns.eventCount(T) : E.events.filter((e) => e.t <= T).length;
    const totalEvents = E.events.length;
    const timelineMax = Math.max(1, finiteTime(E.meta.tMax, 0), finiteTime(E.meta.tNow, 0));
    const activeRun = E.meta.activeAgents == null
      ? E.agents.some((a) => String(a.status || '').toLowerCase() !== 'dead' && a.retiredAt == null)
      : Number(E.meta.activeAgents || 0) > 0;
    const empty = E.meta.totalNodes === 0 && totalEvents === 0 && E.agents.length === 0;
    const atNow = T >= finiteTime(E.meta.tNow, 0) - 1;
    const sparkPath = useMemo(() => {
      const s = E.series;
      const maximize = directionLabel() === 'maximize';
      const values = [E.meta.baseline, E.meta.target].concat(s.map((p) => p.best)).filter((v) => typeof v === 'number' && Number.isFinite(v));
      const lo = values.length ? Math.min(...values) : 0;
      const hi = values.length ? Math.max(...values) : 1;
      const span = Math.max(1e-9, hi - lo);
      return s.map((p, i) => {
        const x = (p.t / timelineMax) * 100;
        const better = p.best == null ? 0 : maximize ? (p.best - lo) / span : (hi - p.best) / span;
        const y = p.best == null ? 92 : 92 - Math.max(0, Math.min(1, better)) * 84;
        return (i === 0 ? 'M' : 'L') + x.toFixed(2) + ' ' + y.toFixed(2);
      }).join(' ');
    }, [E.series, E.meta.baseline, E.meta.target, E.meta.direction, timelineMax]);
    const sparkFill = sparkPath ? sparkPath + ` L 100 100 L 0 100 Z` : '';

    const setFromX = (clientX) => {
      if (!trackRef.current) return;
      const r = trackRef.current.getBoundingClientRect();
      const f = Math.max(0, Math.min(1, (clientX - r.left) / r.width));
      setT(f * timelineMax); setPlaying(false);
    };
    const down = (e) => { dragging.current = true; setFromX(e.clientX); };
    const move = (e) => { if (dragging.current) setFromX(e.clientX); };
    const up = () => { dragging.current = false; };
    useEffect(() => { window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
      return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); }; }, []);

    if (empty) {
      return (
        <div className="scrubber scrubber-empty">
          <button className="btn primary play-btn" disabled={true}>
            ●<span>Live</span></button>
          <div className="scrub-track-wrap">
            <div className="scrub-time mono">
              <span>{'Events ' + eventCount + ' (' + mmss(finiteTime(E.meta.tNow, T)) + ')'}</span>
              <span className="live-tag">● LIVE</span>
            </div>
            <div className="scrub-empty-track">waiting for first run</div>
          </div>
          <button className="btn jump-btn" disabled={true}>Jump to now</button>
        </div>
      );
    }

    const pct = (T / timelineMax) * 100;
    const bornNow = E.fns.bornCount(T);
    const totalBorn = E.fns.bornCount(timelineMax);
    const timelineLabel = bornNow > 0 ? 'Step ' + bornNow : 'Events ' + eventCount;
    const playLabel = activeRun ? (stopStatus === 'stopping' ? 'Stopping' : 'Stop run') : playing ? 'Pause' : 'Replay';
    const playIcon = activeRun ? '■' : playing ? '❚❚' : '▶';
    const primaryClass = 'btn primary play-btn' + (activeRun ? ' danger-live' : '');
    const primaryClick = () => {
      if (activeRun) {
        onStopRun();
        return;
      }
      if (playing) {
        setPlaying(false);
        return;
      }
      if (atNow) setT(0);
      setPlaying(true);
    };

    return (
      <div className="scrubber">
        <button className={primaryClass} onClick={primaryClick} disabled={stopStatus === 'stopping'}>
          {playIcon}<span>{playLabel}</span></button>
        <div className="scrub-track-wrap">
          <div className="scrub-time mono">
            <span>{timelineLabel + ' (' + mmss(T) + ')'}</span>
            {activeRun ? <span className="live-tag">● LIVE</span> : <span className="mono scrub-of">{' of ' + (totalBorn > 0 ? totalBorn + ' steps' : totalEvents + ' events') + ' (' + mmss(timelineMax) + ')'}</span>}
            {stopStatus && stopStatus !== 'stopping' ? <span className="run-control-status">{stopStatus}</span> : null}
          </div>
          <div className="scrub-track" ref={trackRef} onPointerDown={down}>
            {sparkPath ? (
              <svg className="spark" viewBox="0 0 100 100" preserveAspectRatio="none">
                <path d={sparkFill} fill="var(--accent-soft)" />
                <path d={sparkPath} fill="none" stroke="var(--accent)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
              </svg>
            ) : null}
            <div className="scrub-done" style={{ width: pct + '%' }} />
            <div className="scrub-head" style={{ left: pct + '%' }} />
          </div>
        </div>
        <div className="scrub-right">
          <div className="speed-seg">
            {[1, 2, 4].map((s) => <button key={s} className={'speed-btn' + (speed === s ? ' on' : '')} onClick={() => setSpeed(s)}>{s + '×'}</button>)}
          </div>
          <button className={'btn jump-btn' + (atNow ? ' on' : '')} onClick={() => { setT(finiteTime(E.meta.tNow, timelineMax)); setPlaying(false); }}>Jump to now</button>
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
    const ActivityPanel = window.ActivityPanel;
    const TweaksPanel = window.TweaksPanel;
    const TweakSection = window.TweakSection;
    const TweakRadio = window.TweakRadio;
    const TweakColor = window.TweakColor;
    const TweakToggle = window.TweakToggle;
    const [t, setTweak] = window.useTweaks(TWEAK_DEFAULTS);
    const [T, setT] = useState(E ? E.meta.tNow : 0);
    const [playing, setPlaying] = useState(false);
    const [speed, setSpeed] = useState(t.speed || 1);
    const [selected, setSelected] = useState(initialNode() || null);
    const [branchSelection, setBranchSelection] = useState([]);
    const [currentTask, setCurrentTask] = useState(initialTask());
    const [tasks, setTasks] = useState([]);
    const [stopStatus, setStopStatus] = useState('');

    useEffect(() => {
      worldRef.current = world;
      E = world;
    }, [world]);

    useEffect(() => {
      window.__AUTORESEARCH_JOURNAL = currentTask;
      if (currentTask) localStorage.setItem('autoresearch-task', currentTask);
      const url = new URL(window.location.href);
      if (currentTask) url.searchParams.set('journal', currentTask);
      else url.searchParams.delete('journal');
      if (selected) url.searchParams.set('node', selected);
      else url.searchParams.delete('node');
      window.history.replaceState(null, '', url.pathname + url.search + url.hash);
    }, [currentTask, selected]);

    useEffect(() => {
      let stopped = false;
      async function loadTasks() {
        try {
          const res = await fetch(apiUrl('/api/tasks', currentTask, { ts: Date.now() }), { cache: 'no-store' });
          if (!res.ok) return;
          const data = await res.json();
          if (stopped) return;
          const nextTasks = Array.isArray(data.tasks) ? data.tasks : [];
          setTasks(nextTasks);
          if (!currentTask && (data.selected || nextTasks[0])) setCurrentTask(data.selected || nextTasks[0].id);
          else if (currentTask && nextTasks.length && !nextTasks.some((task) => task.id === currentTask)) setCurrentTask(nextTasks[0].id);
        } catch (_) {}
      }
      loadTasks();
      const id = setInterval(loadTasks, 5000);
      return () => { stopped = true; clearInterval(id); };
    }, [currentTask]);

    const loadTaskData = useCallback(async (task) => {
      const res = await fetch(apiUrl('/api/data', task, { ts: Date.now() }), { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.payload && window.__AUTORESEARCH_APPLY_PAYLOAD) {
        window.__AUTORESEARCH_APPLY_PAYLOAD(data.payload);
      }
    }, []);

    const chooseTask = useCallback((task) => {
      setCurrentTask(task);
      setSelected(null);
      setBranchSelection([]);
      setStopStatus('');
      setPlaying(false);
      loadTaskData(task);
    }, [loadTaskData]);

    const stopRun = useCallback(async () => {
      if (!currentTask) return;
      setStopStatus('stopping');
      try {
        const res = await fetch(apiUrl('/api/control/stop-run', currentTask), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: 'Stopped from dashboard' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error(data.error || 'stop failed');
        setStopStatus('stopped');
        await loadTaskData(currentTask);
      } catch (err) {
        setStopStatus(err instanceof Error ? err.message : 'stop failed');
      }
    }, [currentTask, loadTaskData]);

    useEffect(() => {
      if (!currentTask) return;
      let stopped = false;
      async function refreshCurrentTask() {
        if (stopped) return;
        await loadTaskData(currentTask);
      }
      const id = setInterval(refreshCurrentTask, 3000);
      return () => { stopped = true; clearInterval(id); };
    }, [currentTask, loadTaskData]);

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
        setSelected((id) => {
          if (id && next.nodes.some((n) => n.id === id)) return id;
          const preferred = [...next.nodes].reverse().find((n) => n.id && n.id.startsWith('hs-final') && n.media && n.media.timelapse)
            || [...next.nodes].reverse().find((n) => n.media && n.media.timelapse);
          return preferred ? preferred.id : null;
        });
        setBranchSelection((ids) => ids.filter((id) => next.nodes.some((n) => n.id === id)).slice(-2));
      };
      if (window.__AUTORESEARCH_PENDING_PAYLOAD) {
        const pending = window.__AUTORESEARCH_PENDING_PAYLOAD;
        delete window.__AUTORESEARCH_PENDING_PAYLOAD;
        window.__AUTORESEARCH_APPLY_PAYLOAD(pending);
      }
      return () => { delete window.__AUTORESEARCH_APPLY_PAYLOAD; };
    }, []);

    // theme + accent
    useEffect(() => { if (t.theme === 'dark') document.documentElement.dataset.theme = 'dark'; else document.documentElement.removeAttribute('data-theme'); }, [t.theme]);
    useEffect(() => { const a: Record<string, string> = ACCENTS[t.accent] || ACCENTS.blue; Object.entries(a).forEach(([k, v]) => document.documentElement.style.setProperty(k, v)); }, [t.accent]);
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

    const onSelect = useCallback((id, opts: { shift?: boolean } = {}) => {
      setSelected(id);
      setBranchSelection((prev) => {
        if (!opts.shift) return [id];
        const next = prev.filter((x) => x !== id).concat(id);
        return next.slice(-2);
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
            <TaskSelect tasks={tasks} value={currentTask} onChange={chooseTask} />
            <div className="prob">
              <span className="prob-name">{E.meta.problem}</span>
              <span className="prob-sub mono">{directionLabel() + ' ' + E.meta.metric + ' · baseline ' + fmt(E.meta.baseline)}</span>
            </div>
            <ChangelogBadge task={currentTask} />
          </div>
          <StatTiles T={T} />
        </header>

        <div className="body">
          <main className="canvas">
            <EvoTree T={T} selected={selected} onSelect={onSelect} density={t.density} scoreLabels={t.scoreLabels} dimOffLineage={t.dimOffLineage} />
          </main>
          <aside className="sidebar">
            {selected
            ? <InspectorPanel nodeId={selected} T={T} speed={speed} onClose={() => setSelected(null)} onSelect={onSelect} branchSelection={branchSelection} />
              : <div className="sb-split">
                  <TeamPanel T={T} onSelect={onSelect} />
                  <HypothesesPanel T={T} onSelect={onSelect} />
                  {t.showFeed ? <ActivityPanel T={T} onSelect={onSelect} /> : null}
                </div>}
          </aside>
        </div>

        <footer className="bottom">
          <Scrubber T={T} setT={setT} playing={playing} setPlaying={setPlaying} speed={speed} setSpeed={setSpeed} onStopRun={stopRun} stopStatus={stopStatus} />
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
      const task = initialTask();
      const res = await fetch(apiUrl('/api/data', task, { ts: Date.now() }), { cache: 'no-store' });
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
