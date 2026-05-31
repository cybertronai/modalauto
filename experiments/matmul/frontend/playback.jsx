/* MatmulPlayback — fast-forward animation of a candidate's matmul run.
   The 16x16 output grid fills as panels are computed; ops fire in sequence.
   Scrubbable + auto-play. Cells updated imperatively for smoothness. */
(function () {
  const { useState, useRef, useEffect, useMemo } = React;

  function rngFrom(seed) { let a = seed >>> 0; return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

  function buildOps(node) {
    const cand = node.candidate || '';
    // Accept both 3-number IR names (4x2x1) and real 2-number panel names (8x4).
    const m = cand.match(/(\d+)x(\d+)(?:x(\d+))?/);
    let pw = m ? Math.max(1, Math.min(8, +m[1])) : 2;
    let ph = m ? Math.max(1, Math.min(8, +m[2])) : 2;
    if (node.family === 'baseline') { pw = 4; ph = 4; }
    const lifetime = node.family === 'lifetime';
    const ops = [];
    const cellDoneOp = new Array(256).fill(999999);
    for (let r = 0; r < 16; r += pw) {
      for (let c = 0; c < 16; c += ph) {
        const cells = [];
        for (let rr = r; rr < Math.min(16, r + pw); rr++)
          for (let cc = c; cc < Math.min(16, c + ph); cc++) cells.push(rr * 16 + cc);
        ops.push({ type: 'load', label: `load A[${r}:${r + pw}] B[${c}:${c + ph}]`, cells });
        for (let k = 0; k < 4; k++) {
          ops.push({ type: 'mul', label: `mul k=${k} panel`, cells });
          ops.push({ type: 'add', label: `add → acc`, cells });
        }
        if (lifetime) ops.push({ type: 'reuse', label: 'reuse dead T', cells });
        const storeIdx = ops.length;
        ops.push({ type: 'store', label: `store C[${r}:${r + pw},${c}:${c + ph}]`, cells });
        cells.forEach((idx) => (cellDoneOp[idx] = storeIdx));
      }
    }
    return { ops, cellDoneOp, pw, ph };
  }

  function MatmulPlayback({ node, speed, timelapse = null }) {
    // REAL execution trace, fetched live from /api/trace (reads the node's
    // best.ir and runs the experiment's real simulator). Falls back to a
    // representative synthetic run only when no real artifact is available.
    const [real, setReal] = useState(null);        // null=loading/none, object=real trace
    const [tried, setTried] = useState(false);
    useEffect(() => {
      let alive = true;
      setReal(null); setTried(false);
      const base = (window.FRONTEND_API_URL || '').replace(/\/$/, '');
      fetch(base + '/api/trace?node=' + encodeURIComponent(node.id) + '&ts=' + Date.now(), { cache: 'no-store' })
        .then((r) => r.ok ? r.json() : null)
        .then((tr) => { if (alive) { setReal(tr && tr.ok && tr.cellDoneOp ? tr : null); setTried(true); } })
        .catch(() => { if (alive) setTried(true); });
      return () => { alive = false; };
    }, [node.id]);

    const synth = useMemo(() => buildOps(node), [node.id]);
    const isReal = !!real;
    const nCells = isReal ? real.cellDoneOp.length : 256;
    // unified "timeline": real uses true op count, synth uses its op list length
    const total = isReal ? Math.max(1, real.totalOps) : synth.ops.length;
    // real runs are thousands of ops — advance in ~240 frames regardless
    const stepSize = isReal ? Math.max(1, Math.round(total / 240)) : 1;

    const [head, setHead] = useState(0);
    const [playing, setPlaying] = useState(true);
    const cellRefs = useRef([]);
    const barRef = useRef(null);
    const traceRef = useRef(null);
    const fitCol = node.fit != null ? node.fit : 3;

    useEffect(() => { setHead(0); }, [node.id, isReal]);

    // imperative cell paint
    useEffect(() => {
      if (isReal) {
        const done = real.cellDoneOp;
        for (let i = 0; i < nCells; i++) {
          const el = cellRefs.current[i]; if (!el) continue;
          const d = done[i];
          if (d >= 0 && d <= head && d > head - stepSize) { el.style.background = 'var(--accent)'; el.style.opacity = '1'; }
          else if (d >= 0 && d <= head) { el.style.background = `var(--fit-${fitCol})`; el.style.opacity = '0.5'; }
          else { el.style.background = 'var(--surface-inset)'; el.style.opacity = '1'; }
        }
      } else {
        const cur = synth.ops[head];
        const active = new Set(cur ? cur.cells : []);
        for (let i = 0; i < 256; i++) {
          const el = cellRefs.current[i]; if (!el) continue;
          if (active.has(i)) { el.style.background = 'var(--accent)'; el.style.opacity = '1'; }
          else if (synth.cellDoneOp[i] <= head) { el.style.background = `var(--fit-${fitCol})`; el.style.opacity = '0.5'; }
          else { el.style.background = 'var(--surface-inset)'; el.style.opacity = '1'; }
        }
      }
      if (barRef.current) barRef.current.style.width = ((head / Math.max(1, total - 1)) * 100) + '%';
    }, [head, isReal, real, synth, fitCol, nCells, total, stepSize]);

    // auto-advance
    useEffect(() => {
      if (!playing) return;
      const iv = setInterval(() => {
        setHead((h) => (h >= total - 1 ? 0 : Math.min(total - 1, h + stepSize)));
      }, Math.max(28, 90 / (speed || 1)));
      return () => clearInterval(iv);
    }, [playing, total, stepSize, speed]);

    // trace ticker window
    let traceWindow = [];
    if (isReal) {
      const tick = real.ticker || [];
      const upto = tick.filter((o) => o.i <= head);
      const start = Math.max(0, upto.length - 6);
      traceWindow = upto.slice(start).map((o) => ({ i: o.i, op: { type: o.type, label: o.label } }));
    } else {
      for (let i = Math.max(0, head - 5); i <= Math.min(synth.ops.length - 1, head + 2); i++) traceWindow.push({ i, op: synth.ops[i] });
    }
    const pctFilled = Math.round((head / Math.max(1, total - 1)) * 100);

    if (node.outcome === 'reject') {
      return (
        <div className="run-empty">
          <div className="run-empty-icon">✕</div>
          <div>No valid run — candidate was rejected</div>
          <div className="mono run-empty-sub">{'semantic: ' + node.semantic}</div>
        </div>
      );
    }

    const onGridRef = (el) => {
      if (el && el.childElementCount === 0) {
        for (let i = 0; i < 256; i++) { const dd = document.createElement('div'); dd.className = 'rc'; el.appendChild(dd); cellRefs.current[i] = dd; }
      }
    };
    const onBarClick = (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      setHead(Math.round(((e.clientX - r.left) / r.width) * (total - 1)));
      setPlaying(false);
    };

    const srcLabel = timelapse
      ? 'branch timelapse available'
      : isReal
      ? ('real trace · ' + (real.totalOps || 0).toLocaleString() + ' ops' + (real.energy != null ? ' · ' + real.energy.toLocaleString() + ' energy' : ''))
      : 'representative run';

    return (
      <div className="run">
        <div className="run-grid" ref={onGridRef} />
        <div className="run-controls">
          <button className="btn icon" onClick={() => setPlaying((p) => !p)}>{playing ? '❚❚' : '▶'}</button>
          <div className="run-bar" onClick={onBarClick}>
            <div className="run-bar-fill" ref={barRef} />
          </div>
          <span className="run-count">{head + '/' + (total - 1)}</span>
        </div>
        <div className="eyebrow" style={{ marginTop: 4 }}>{'op trace · ' + pctFilled + '% · ' + srcLabel}</div>
        <div className="run-trace" ref={traceRef}>
          {traceWindow.map(({ i, op }) => (
            <div key={i} className={'trace-line' + (i === head ? ' on' : '')} style={{ opacity: i === head ? 1 : 0.4 }}>
              <span className="trace-k">{'op' + String(i).padStart(3, '0')}</span>
              <span className={'trace-t t-' + op.type}>{op.type}</span>
              <span className="trace-lbl">{op.label}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  window.MatmulPlayback = MatmulPlayback;
  window.RunPlayback = MatmulPlayback;
})();
