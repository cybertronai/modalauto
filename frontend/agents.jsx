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

  // --- Team section ---
  function RoleGroup({ role, agents, act, onSelect }) {
    const [open, setOpen] = useState(true);
    const working = agents.filter((a) => act[a.id].status === 'working').length;
    return (
      <div className={'fp-grp' + (open ? '' : ' collapsed')}>
        <div className="fp-grp-head" onClick={() => setOpen((o) => !o)}>
          <Chevron />
          <span className="fp-grp-name" style={{ color: roleCol(role) }}>{ROLE_LABEL[role]}</span>
          <span className="fp-grp-count">{working ? working + '/' + agents.length : agents.length}</span>
        </div>
        {open ? agents.map((a) => <AgentRow key={a.id} a={a} act={act[a.id]} onSelect={onSelect} />) : null}
      </div>
    );
  }

  function AgentRow({ a, act, onSelect }) {
    const working = act.status === 'working';
    return (
      <div className={'agent-row' + (working ? ' is-working' : '')}>
        <span className={'ar-dot' + (working ? ' pulsing' : '')} style={{ background: working ? roleCol(a.role) : 'var(--idle)' }} />
        <span className="ar-name">{a.id}</span>
        {working
          ? <span className="ar-act busy" onClick={act.item ? () => onSelect(act.item) : null} title={act.kind}>{act.kind}</span>
          : <span className="ar-act idle">idle</span>}
      </div>
    );
  }

  function TeamPanel({ T, onSelect }) {
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
            {ROLE_ORDER.map((r) => grouped[r].length ? <RoleGroup key={r} role={r} agents={grouped[r]} act={act} onSelect={onSelect} /> : null)}
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

  // --- Activity feed ---
  function ActivityPanel({ T, onSelect }) {
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
            {feed.map((e, i) => <ActRow key={e.t + '-' + i + '-' + (e.nodeId || e.kind)} e={e} onSelect={onSelect} />)}
          </div>
        ) : null}
      </div>
    );
  }

  function ActRow({ e, onSelect }) {
    const col = e.role ? roleCol(e.role) : 'var(--ink-3)';
    let body;
    if (e.kind === 'verified') body = <span><b>{e.decision === 'accept' ? 'accepted' : 'rejected'}</b>{' '}{e.score != null ? <span className="mono">{e.score.toLocaleString()}</span> : 'invalid'}</span>;
    else if (e.kind === 'submitted') body = <span><b>submitted</b>{e.score != null ? <span className="mono" style={{ marginLeft: 5 }}>{'~' + e.score.toLocaleString()}</span> : null}</span>;
    else if (e.kind === 'spawn') body = <span>{e.text}</span>;
    else if (e.kind === 'research') body = <span><b>indexed</b>{' '}{e.text}</span>;
    else if (e.kind === 'scale') body = <span>applied scale plan</span>;
    else if (e.kind === 'proposed') body = <span><b>proposed</b><span style={{ color: 'var(--ink-2)' }}>{' — ' + e.text}</span></span>;
    else if (e.kind === 'claimed') body = <span><b>claimed</b></span>;
    else body = <span>{e.kind}</span>;
    return (
      <div className={'act-row' + (e.nodeId ? ' clickable' : '')} onClick={e.nodeId ? () => onSelect(e.nodeId) : null}>
        <span className="act-dot" style={{ background: col }} />
        <div className="act-body">
          <div className="act-line"><span className="act-agent" style={{ color: col }}>{e.agent}</span>{' '}{body}</div>
          <div className="act-meta">{mmss(e.t)}{e.nodeId ? ' · ' + e.nodeId : ''}</div>
        </div>
      </div>
    );
  }

  function mmss(t) {
    const safe = typeof t === 'number' && Number.isFinite(t) ? Math.max(0, t) : 0;
    const total = Math.round(safe);
    return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
  }

  Object.assign(window, { TeamPanel, HypothesesPanel, ActivityPanel, SBSection: Section, SBChevron: Chevron, SBRolePill: RolePill,
    EVO_ROLE_LABEL: ROLE_LABEL, EVO_ROLE_VAR: ROLE_VAR, evoRoleCol: roleCol, evoMMSS: mmss });
})();
