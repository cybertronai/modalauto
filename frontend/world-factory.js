(function () {
  function appWorld(payload) {
    const maximize = String(payload.meta.direction || 'minimize').toLowerCase() === 'maximize';
    payload.traces = Array.isArray(payload.traces) ? payload.traces : [];
    payload.traceById = {};
    payload.tracesByAgent = {};
    payload.tracesByItem = {};
    payload.tracesByRun = {};
    payload.traces.forEach((tr) => {
      payload.traceById[tr.id] = tr;
      if (tr.agentId) (payload.tracesByAgent[tr.agentId] || (payload.tracesByAgent[tr.agentId] = [])).push(tr);
      if (tr.itemId) (payload.tracesByItem[tr.itemId] || (payload.tracesByItem[tr.itemId] = [])).push(tr);
      if (tr.runId) (payload.tracesByRun[tr.runId] || (payload.tracesByRun[tr.runId] = [])).push(tr);
    });
    function statusAt(n, T) {
      if (T < n.tProposed) return 'unborn';
      if (n.abandoned) return T > n.tProposed + 18 ? 'abandoned' : 'queued';
      if (n.tClaimed == null || T < n.tClaimed) return 'queued';
      if (n.tSubmitted == null || T < n.tSubmitted) return 'claimed';
      if (n.tVerified == null || T < n.tVerified) return 'submitted';
      return n.outcome === 'accept' ? 'verified' : 'rejected';
    }
    function bornCount(T) {
      return payload.nodes.filter((n) => n.tVerified != null && n.tVerified <= T).length;
    }
    function frontierAt(T) {
      let best = null;
      payload.nodes.forEach((n) => {
        if (n.outcome === 'accept' && n.score != null && n.tVerified != null && n.tVerified <= T) {
          best = best == null ? n.score : (maximize ? Math.max(best, n.score) : Math.min(best, n.score));
        }
      });
      return best;
    }
    function fitBin(score) {
      if (score == null) return null;
      const best = payload.meta.best == null ? payload.meta.baseline : payload.meta.best;
      if (maximize) {
        const span = Math.max(1e-9, best - payload.meta.baseline);
        return Math.max(0, Math.min(6, Math.round(((score - payload.meta.baseline) / span) * 6)));
      }
      const span = Math.max(1e-9, payload.meta.baseline - best);
      return Math.max(0, Math.min(6, Math.round(((payload.meta.baseline - score) / span) * 6)));
    }
    function agentActivity(T) {
      const out = {};
      payload.agents.forEach((a) => {
        const alive = a.spawnedAt <= T && (a.retiredAt == null || a.retiredAt > T);
        out[a.id] = { id: a.id, role: a.role, alive, status: 'idle', item: null, kind: null };
      });
      payload.nodes.forEach((n) => {
        if (n.impl && n.tClaimed != null && n.tClaimed <= T && (n.tSubmitted == null || T < n.tSubmitted)) {
          const o = out[n.impl]; if (o && o.alive) { o.status = 'working'; o.item = n.id; o.kind = 'building ' + n.candidate; }
        }
        if (n.verifier && n.tSubmitted != null && n.tSubmitted <= T && (n.tVerified == null || T < n.tVerified)) {
          const o = out[n.verifier]; if (o && o.alive) { o.status = 'working'; o.item = n.id; o.kind = 'verifying ' + (n.subId || 'submission'); }
        }
        if (n.proposer && T >= n.tProposed - 2.5 && T < n.tProposed) {
          const o = out[n.proposer]; if (o && o.alive && o.status === 'idle') { o.status = 'working'; o.item = n.id; o.kind = 'proposing hypothesis'; }
        }
      });
      payload.events.forEach((e) => {
        if (e.t > T || T - e.t >= 4 || !e.agent || !out[e.agent]) return;
        const o = out[e.agent];
        if (!o.alive || o.status === 'working') return;
        if (e.kind === 'scale') { o.status = 'working'; o.kind = 'planning scale'; }
        if (e.kind === 'spawn') { o.status = 'working'; o.kind = 'starting'; }
      });
      return out;
    }
    payload.fns = { statusAt, bornCount, agentActivity, frontierAt, fitBin };
    return payload;
  }
  window.appWorld = appWorld;
})();
