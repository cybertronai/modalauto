/* Sidebar content — Figma-style properties panel.
   Team (collapsible role groups) + Activity feed. Exports a shared
   collapsible Section + Chevron + role helpers used by the inspector too. */
(function () {
  const { useState, useMemo, useRef, useEffect } = React;
  let E = window.APP;
  window.addEventListener('autoresearch-world', () => { E = window.APP; });

  const ROLE_ORDER = ['topline_manager', 'meta_agent', 'insight_generator', 'creative_explorer', 'global_searcher', 'implementor', 'verifier', 'researcher'];
  const ROLE_LABEL = { topline_manager: 'manager', meta_agent: 'meta', insight_generator: 'insight', creative_explorer: 'explorer', global_searcher: 'searcher', implementor: 'implementor', verifier: 'verifier', researcher: 'researcher' };
  const ROLE_VAR = { topline_manager: '--role-manager', meta_agent: '--role-meta', insight_generator: '--role-insight', creative_explorer: '--role-explorer', global_searcher: '--role-searcher', implementor: '--role-implementor', verifier: '--role-verifier', researcher: '--role-researcher' };
  const roleCol = (r) => `var(${ROLE_VAR[r]})`;

  function Chevron({ className }) {
    return (
      <svg className={'sb-chev ' + (className || '')} viewBox="0 0 16 16" fill="none">
        <path d="M5 6 L8 9.5 L11 6" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  // collapsible section
  function Section({ title, aside, defaultOpen = true, children, flush }) {
    const [open, setOpen] = useState(defaultOpen);
    return (
      <div className="sb-section">
        <div className={'sb-head' + (open ? '' : ' collapsed')} onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <h3>{title}</h3>
          {aside != null ? <span className="sb-aside">{aside}</span> : null}
        </div>
        {open ? <div className="sb-body" style={flush ? { marginLeft: -8, marginRight: -8 } : null}>{children}</div> : null}
      </div>
    );
  }

  function RolePill({ role }) {
    return <span className="role-chip" style={{ '--role': roleCol(role) }}><span className="glyph" />{ROLE_LABEL[role]}</span>;
  }

  function traceFor(id) {
    return id && E.traceById ? E.traceById[id] : null;
  }

  function fmtDuration(ms) {
    if (ms == null) return 'running';
    if (ms < 1000) return ms + ' ms';
    return (ms / 1000).toFixed(ms < 10000 ? 2 : 1) + ' s';
  }

  function TraceButton({ traceId, onTrace }) {
    if (!traceId) return null;
    const tr = traceFor(traceId);
    return (
      <button className={'trace-chip' + (tr && tr.status === 'failed' ? ' failed' : '')}
        title={tr ? 'View trace · ' + tr.title : 'View trace'}
        onClick={(e) => { e.stopPropagation(); onTrace(traceId); }}>
        trace
      </button>
    );
  }

  function TracePanel({ traceId, onClose, onSelect }) {
    const trace = traceFor(traceId);
    if (!trace) {
      return (
        <React.Fragment>
          <div className="insp-bar">
            <button className="insp-back" onClick={onClose}>team</button>
            <span className="insp-id">missing trace</span>
          </div>
          <div className="sb-scroll"><div className="empty-pane">Trace is no longer in the live payload.</div></div>
        </React.Fragment>
      );
    }
    const spans = Array.isArray(trace.spans) ? trace.spans : [];
    const maxMs = Math.max(1, ...spans.map((s) => s.durationMs || 0));
    return (
      <React.Fragment>
        <div className="insp-bar">
          <button className="insp-back" onClick={onClose}>
            <svg width={13} height={13} viewBox="0 0 16 16" fill="none">
              <path d="M10 4 L6 8 L10 12" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            team
          </button>
          <span className="insp-id">{trace.id}</span>
          <span className={'trace-status ' + trace.status}>{trace.status}</span>
        </div>
        <div className="sb-scroll">
          <div className="sb-section">
            <h3 className="insp-title">{trace.title}</h3>
            <div className="insp-sub">
              {trace.role ? <RolePill role={trace.role} /> : null}
              <span className="insp-meta mono">{trace.agentId || 'unknown agent'}</span>
              <span className="insp-meta mono">{fmtDuration(trace.durationMs)}</span>
            </div>
          </div>
          <Section title="Trace summary" defaultOpen={true}>
            <div className="artifact-metrics">
              <div className="prow"><span className="pk">Kind</span><span className="pv">{trace.kind}</span></div>
              <div className="prow"><span className="pk">Item</span><span className="pv">{trace.itemId || '—'}</span></div>
              <div className="prow"><span className="pk">Run</span><span className="pv">{trace.runId || '—'}</span></div>
              <div className="prow"><span className="pk">Started</span><span className="pv">{trace.startedAtIso || '—'}</span></div>
              {trace.error ? <div className="prow"><span className="pk">Error</span><span className="pv bad">{trace.error}</span></div> : null}
            </div>
            <div className="control-row trace-actions">
              {trace.itemId ? <button className="btn" onClick={() => onSelect(trace.itemId)}>Open item</button> : null}
              {trace.workshopUrl ? <a className="btn" href={trace.workshopUrl} target="_blank" rel="noreferrer">Open Workshop</a> : null}
            </div>
          </Section>
          <Section title="Spans" aside={spans.length} defaultOpen={true}>
            <div className="agent-trace-list">
              {spans.length ? spans.map((span) => (
                <div key={span.id} className={'agent-trace-row ' + (span.status || 'ok')}>
                  <div className="agent-trace-top">
                    <span className="agent-trace-name">{span.name}</span>
                    <span className="agent-trace-kind">{span.kind}</span>
                    <span className="agent-trace-ms">{fmtDuration(span.durationMs)}</span>
                  </div>
                  <div className="agent-trace-bar"><span style={{ width: Math.max(2, ((span.durationMs || 0) / maxMs) * 100) + '%' }} /></div>
                  {span.error ? <div className="agent-trace-error">{span.error}</div> : null}
                  {(span.metadata && Object.keys(span.metadata).length) || span.output
                    ? <details className="agent-trace-details">
                        <summary>payload</summary>
                        <pre>{JSON.stringify({ metadata: span.metadata, output: span.output }, null, 2)}</pre>
                      </details>
                    : null}
                </div>
              )) : <div className="insp-meta">No child spans recorded.</div>}
            </div>
          </Section>
          {trace.metadata && Object.keys(trace.metadata).length ? <Section title="Metadata" defaultOpen={false}>
            <pre className="artifact-preview">{JSON.stringify(trace.metadata, null, 2)}</pre>
          </Section> : null}
        </div>
      </React.Fragment>
    );
  }

  // --- Team section ---
  function RoleGroup({ role, agents, act, onSelect, onTrace }) {
    const [open, setOpen] = useState(true);
    const working = agents.filter((a) => act[a.id].status === 'working').length;
    return (
      <div className={'fp-grp' + (open ? '' : ' collapsed')}>
        <div className="fp-grp-head" onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <span className="fp-grp-name" style={{ color: roleCol(role) }}>{ROLE_LABEL[role]}</span>
          <span className="fp-grp-count">{working ? working + '/' + agents.length : agents.length}</span>
        </div>
        {open ? agents.map((a) => <AgentRow key={a.id} a={a} act={act[a.id]} onSelect={onSelect} onTrace={onTrace} />) : null}
      </div>
    );
  }

  function AgentRow({ a, act, onSelect, onTrace }) {
    const working = act.status === 'working';
    const traceId = a.lastTraceId || (a.traceIds && a.traceIds[a.traceIds.length - 1]);
    return (
      <div className={'agent-row' + (working ? ' is-working' : '') + (traceId ? ' has-trace' : '')} onClick={traceId ? () => onTrace(traceId) : null}>
        <span className={'ar-dot' + (working ? ' pulsing' : '')} style={{ background: working ? roleCol(a.role) : 'var(--idle)' }} />
        <span className="ar-name">{a.id}</span>
        {working
          ? <span className="ar-act busy" onClick={act.item ? (e) => { e.stopPropagation(); onSelect(act.item); } : null} title={act.kind}>{act.kind}</span>
          : <span className="ar-act idle">idle</span>}
        <TraceButton traceId={traceId} onTrace={onTrace} />
      </div>
    );
  }

  function TeamPanel({ T, onSelect, onTrace }) {
    const [open, setOpen] = useState(true);
    const act = useMemo(() => E.fns.agentActivity(T), [T]);
    const live = E.agents.filter((a) => act[a.id] && act[a.id].alive);
    const working = live.filter((a) => act[a.id].status === 'working').length;
    const grouped = {}; ROLE_ORDER.forEach((r) => (grouped[r] = []));
    live.forEach((a) => grouped[a.role] && grouped[a.role].push(a));

    return (
      <div className={'sb-pane sb-pane-team' + (open ? '' : ' collapsed')}>
        <div className="pane-head" onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <h3>Live team</h3>
          <span className="pane-aside">{live.length + ' agents'}{working ? <span className="pane-working">{' · ' + working + ' working'}</span> : ' · idle'}</span>
        </div>
        {open ? (
          <div className="pane-scroll">
            {ROLE_ORDER.map((r) => grouped[r].length ? <RoleGroup key={r} role={r} agents={grouped[r]} act={act} onSelect={onSelect} onTrace={onTrace} /> : null)}
          </div>
        ) : null}
      </div>
    );
  }

  function HypothesesPanel({ T, onSelect }) {
    const [open, setOpen] = useState(true);
    const items = useMemo(() => {
      return (E.hypotheses || [])
        .filter((h) => h.createdAt <= T)
        .sort((a, b) => {
          if (a.inTree !== b.inTree) return a.inTree ? 1 : -1;
          if (a.hasSubmission !== b.hasSubmission) return a.hasSubmission ? 1 : -1;
          return b.createdAt - a.createdAt;
        })
        .slice(0, 120);
    }, [T]);
    const active = items.filter((h) => !h.inTree).length;
    return (
      <div className={'sb-pane sb-pane-hypotheses' + (open ? '' : ' collapsed')}>
        <div className="pane-head" onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <h3>Hypotheses</h3>
          <span className="pane-aside">{active + ' active · ' + items.length + ' total'}</span>
        </div>
        {open ? (
          <div className="pane-scroll hyp-list">
            {items.length
              ? items.map((h) => <HypothesisRow key={h.id} h={h} onSelect={onSelect} />)
              : <div className="empty-pane">No hypotheses yet</div>}
          </div>
        ) : null}
      </div>
    );
  }

  function HypothesisRow({ h, onSelect }) {
    const col = h.proposerRole ? roleCol(h.proposerRole) : 'var(--ink-3)';
    const clickable = h.inTree && h.hasSubmission;
    const label = h.inTree ? 'tree' : h.hasSubmission ? 'context' : h.status;
    return (
      <div className={'hyp-row' + (clickable ? ' clickable' : '')} onClick={clickable ? () => onSelect(h.id) : null}>
        <span className="hyp-dot" style={{ background: h.inTree ? col : 'transparent', borderColor: col }} />
        <div className="hyp-main">
          <div className="hyp-title">{h.title}</div>
          <div className="hyp-meta">
            <span style={{ color: col }}>{ROLE_LABEL[h.proposerRole] || h.proposerRole || 'unknown'}</span>
            <span>{' · ' + mmss(h.createdAt)}</span>
            {h.parent ? <span>{' · parent ' + h.parent.slice(4, 10)}</span> : null}
          </div>
        </div>
        <span className={'hyp-state' + (h.inTree ? ' in-tree' : '')}>{label}</span>
      </div>
    );
  }

  function TracesPanel({ T, onTrace }) {
    const [open, setOpen] = useState(true);
    const traces = useMemo(() => {
      return (E.traces || [])
        .filter((tr) => tr.startedAt == null || tr.startedAt <= T)
        .slice(-80)
        .reverse();
    }, [T]);
    return (
      <div className={'sb-pane sb-pane-traces' + (open ? '' : ' collapsed')}>
        <div className="pane-head" onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <h3>Agent traces</h3>
          <span className="pane-aside">{traces.length + ' recent'}</span>
        </div>
        {open ? (
          <div className="pane-scroll trace-link-list">
            {traces.length ? traces.map((tr) => (
              <button key={tr.id} className="trace-link-row" onClick={() => onTrace(tr.id)}>
                <span className={'trace-status ' + (tr.status || 'ok')}>{tr.status || 'trace'}</span>
                <span className="trace-link-title">{tr.title || tr.kind || tr.id}</span>
                <span className="trace-link-meta">{tr.role || tr.agentId || 'agent'}</span>
              </button>
            )) : <div className="empty-pane">No traces yet</div>}
          </div>
        ) : null}
      </div>
    );
  }

  // --- Activity feed ---
  function ActivityPanel({ T, onSelect, onTrace }) {
    const [open, setOpen] = useState(true);
    const feed = useMemo(() => E.events.filter((e) => e.t <= T).slice(-100).reverse(), [T]);
    return (
      <div className={'sb-pane sb-pane-activity' + (open ? '' : ' collapsed')}>
        <div className="pane-head" onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <h3>Activity</h3>
          <span className="pane-aside">{'message board · ' + feed.length}</span>
        </div>
        {open ? (
          <div className="pane-scroll">
            {feed.map((e, i) => <ActRow key={e.t + '-' + i + '-' + (e.traceId || e.nodeId || e.kind)} e={e} onSelect={onSelect} onTrace={onTrace} />)}
          </div>
        ) : null}
      </div>
    );
  }

  function ActRow({ e, onSelect, onTrace }) {
    const col = e.role ? roleCol(e.role) : 'var(--ink-3)';
    let body;
    if (e.kind === 'verified') body = <span><b>{e.decision === 'accept' ? 'accepted' : 'rejected'}</b>{' '}{e.score != null ? <span className="mono">{e.score.toLocaleString()}</span> : 'invalid'}</span>;
    else if (e.kind === 'submitted') body = <span><b>submitted</b>{e.score != null ? <span className="mono" style={{ marginLeft: 5 }}>{'~' + e.score.toLocaleString()}</span> : null}</span>;
    else if (e.kind === 'spawn') body = <span>{e.text}</span>;
    else if (e.kind === 'research') body = <span><b>indexed</b>{' '}{e.text}</span>;
    else if (e.kind === 'scale') body = <span>applied scale plan</span>;
    else if (e.kind === 'trace') body = <span><b>trace</b><span style={{ color: 'var(--ink-2)' }}>{' — ' + e.text}</span></span>;
    else if (e.kind === 'proposed') body = <span><b>proposed</b><span style={{ color: 'var(--ink-2)' }}>{' — ' + e.text}</span></span>;
    else if (e.kind === 'claimed') body = <span><b>claimed</b></span>;
    else body = <span>{e.kind}</span>;
    const clickable = e.traceId || e.nodeId;
    return (
      <div className={'act-row' + (clickable ? ' clickable' : '')} onClick={e.traceId ? () => onTrace(e.traceId) : e.nodeId ? () => onSelect(e.nodeId) : null}>
        <span className="act-dot" style={{ background: col }} />
        <div className="act-body">
          <div className="act-line"><span className="act-agent" style={{ color: col }}>{e.agent}</span>{' '}{body}</div>
          <div className="act-meta">{mmss(e.t)}{e.nodeId ? ' · ' + e.nodeId : ''}{e.traceId ? ' · ' + e.traceId : ''}</div>
        </div>
      </div>
    );
  }

  function mmss(t) {
    const safe = typeof t === 'number' && Number.isFinite(t) ? Math.max(0, t) : 0;
    const total = Math.round(safe);
    return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
  }

  Object.assign(window, { TeamPanel, HypothesesPanel, TracesPanel, ActivityPanel, TracePanel, SBSection: Section, SBChevron: Chevron, SBRolePill: RolePill,
    EVO_ROLE_LABEL: ROLE_LABEL, EVO_ROLE_VAR: ROLE_VAR, evoRoleCol: roleCol, evoMMSS: mmss });
})();
