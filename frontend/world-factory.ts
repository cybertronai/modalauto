(function () {
  function appWorld(payload: ResearchWorld): ResearchWorld {
    const maximize = String(payload.meta.direction || 'minimize').toLowerCase() === 'maximize';
    function statusAt(n: ResearchNode, T: number): NodeStatus {
      if (T < n.tProposed) return 'unborn';
      if (n.abandoned) return T > n.tProposed + 18 ? 'abandoned' : 'queued';
      if (n.tClaimed == null || T < n.tClaimed) return 'queued';
      if (n.tSubmitted == null || T < n.tSubmitted) return 'claimed';
      if (n.tVerified == null || T < n.tVerified) return 'submitted';
      return n.outcome === 'accept' ? 'verified' : 'rejected';
    }
    function bornCount(T: number): number {
      return payload.nodes.filter((n) => n.tVerified != null && n.tVerified <= T).length;
    }
    function eventCount(T: number): number {
      return payload.events.filter((e) => e.t <= T).length;
    }
    function frontierAt(T: number): number | null {
      let best: number | null = null;
      payload.nodes.forEach((n) => {
        if (n.outcome === 'accept' && n.score != null && n.tVerified != null && n.tVerified <= T) {
          best = best == null ? n.score : (maximize ? Math.max(best, n.score) : Math.min(best, n.score));
        }
      });
      return best;
    }
    function fitBin(score: number | null | undefined): number | null {
      if (score == null) return null;
      const baseline = typeof payload.meta.baseline === 'number' ? payload.meta.baseline : 0;
      const best = typeof payload.meta.best === 'number' ? payload.meta.best : baseline;
      if (maximize) {
        const span = Math.max(1e-9, best - baseline);
        return Math.max(0, Math.min(6, Math.round(((score - baseline) / span) * 6)));
      }
      const span = Math.max(1e-9, baseline - best);
      return Math.max(0, Math.min(6, Math.round(((baseline - score) / span) * 6)));
    }
    function agentActivity(T: number): Record<string, AgentActivity> {
      const out: Record<string, AgentActivity> = {};
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
    payload.fns = { statusAt, bornCount, eventCount, agentActivity, frontierAt, fitBin };
    return payload;
  }
  window.appWorld = appWorld;
})();
