/* Matmul visualization components. Registered by type so shared UI panels do
   not need matmul-specific rendering branches. */
(function () {
  const { useEffect, useState, Fragment } = React;

  const fmt = (n) => {
    if (n == null) return '—';
    if (typeof n !== 'number') return String(n);
    if (!Number.isFinite(n)) return String(n);
    const abs = Math.abs(n);
    const maximumFractionDigits = Number.isInteger(n) ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
    return n.toLocaleString(undefined, { maximumFractionDigits });
  };

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

  function MatmulIR({ node }) {
    return (
      <Fragment>
        <pre className="well ir" style={{ marginBottom: 10 }}>{genIR(node)}</pre>
        <VerificationRecord node={node} />
      </Fragment>
    );
  }

  function MatmulPlayback({ node, speed }) {
    const Playback = window.MatmulPlayback || window.RunPlayback;
    return Playback ? <Playback node={node} speed={speed} timelapse={node.media && node.media.timelapse} /> : null;
  }

  function MatmulMetrics({ node }) {
    const [ops, setOps] = useState(null);
    useEffect(() => {
      let alive = true;
      setOps(null);
      const base = (window.FRONTEND_API_URL || '').replace(/\/$/, '');
      fetch(base + '/api/trace?node=' + encodeURIComponent(node.id) + '&ts=' + Date.now(), { cache: 'no-store' })
        .then((r) => r.ok ? r.json() : null)
        .then((t) => { if (alive) setOps(t && t.ok ? t.totalOps : null); })
        .catch(() => {});
      return () => { alive = false; };
    }, [node.id]);
    const perOp = (ops && node.score != null) ? node.score / ops : null;
    return (
      <div className="snap-m">
        <span className="snap-mk">energy / op</span>
        <span className="snap-mv mono">{perOp == null ? '…' : perOp.toFixed(1)}</span>
      </div>
    );
  }

  function MatmulCode({ node }) {
    if (node.code) {
      return (
        <Fragment>
          <div className="block-label" style={{ margin: '4px 0 6px' }}>{'candidate code · ' + (node.codeLang || 'python')}</div>
          <pre className="well ir">{node.code}</pre>
        </Fragment>
      );
    }
    return (
      <Fragment>
        <div className="block-label" style={{ margin: '4px 0 6px' }}>candidate IR · representative</div>
        <pre className="well ir">{genIR(node)}</pre>
      </Fragment>
    );
  }

  window.AutoresearchVisualizations = Object.assign(window.AutoresearchVisualizations || {}, {
    matmul_ir: MatmulIR,
    matmul_playback: MatmulPlayback,
    matmul_metrics: MatmulMetrics,
    matmul_code: MatmulCode,
  });
})();
