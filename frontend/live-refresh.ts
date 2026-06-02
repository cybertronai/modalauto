(function () {
  const UI = window.AutoresearchUI;
  const params = new URLSearchParams(window.location.search);
  if (params.get('live') === '0') return;

  let inFlight = false;
  let lastHash: string | null = null;
  let lastFetchAt = 0;
  let events: EventSource | null = null;
  let eventsTask: string | null = null;

  function applyPayload(data: any): void {
    if (!data || !data.payload) return;
    const activeAgents = Number(data.payload.meta && data.payload.meta.activeAgents || 0);
    if (data.hash && data.hash === lastHash && activeAgents <= 0) return;
    if (data.hash) lastHash = data.hash;
    if (window.__AUTORESEARCH_APPLY_PAYLOAD) {
      window.__AUTORESEARCH_APPLY_PAYLOAD(data.payload, data);
    } else {
      window.__AUTORESEARCH_PENDING_PAYLOAD = data.payload;
      window.__AUTORESEARCH_PENDING_DATA = data;
    }
    window.dispatchEvent(new CustomEvent('autoresearch-refresh', { detail: data }));
  }

  async function check(force?: boolean): Promise<void> {
    if (inFlight) return;
    if (!force && Date.now() - lastFetchAt < 2500) return;
    inFlight = true;
    lastFetchAt = Date.now();
    try {
      const res = await fetch(UI.apiUrl('/api/data', UI.currentTask(), { ts: Date.now() }), { cache: 'no-store' });
      if (!res.ok) return;
      applyPayload(await res.json());
    } catch (_) {
      // Keep the static fallback usable if the dynamic dev server is not running.
    } finally {
      inFlight = false;
    }
  }

  function connectEvents(): void {
    if (!('EventSource' in window) || params.get('sse') === '0') return;
    const task = UI.currentTask();
    if (events && eventsTask === task) return;
    if (events) events.close();
    try {
      events = new EventSource(UI.apiUrl('/api/events', task));
      eventsTask = task;
      events.addEventListener('change', () => check(true));
      events.addEventListener('missing', () => check(true));
      events.onerror = () => {};
      window.__AUTORESEARCH_EVENTS = events;
    } catch (_) {}
  }

  if ('EventSource' in window && params.get('sse') !== '0') {
    try {
      const source = new EventSource('/api/dev-events');
      source.addEventListener('reload', () => window.location.reload());
      source.onerror = () => {};
      window.__AUTORESEARCH_DEV_EVENTS = source;
    } catch (_) {}
  }

  connectEvents();
  check(true);
  setInterval(() => {
    connectEvents();
    check();
  }, 3000);
})();
