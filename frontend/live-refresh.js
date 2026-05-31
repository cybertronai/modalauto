(function () {
  const params = new URLSearchParams(window.location.search);
  if (params.get('live') === '0') return;

  let inFlight = false;
  let lastHash = null;
  let lastFetchAt = 0;
  let events = null;
  let eventsTask = null;

  function currentTask() {
    return window.__AUTORESEARCH_JOURNAL
      || new URLSearchParams(window.location.search).get('journal')
      || localStorage.getItem('autoresearch-task')
      || '';
  }

  function apiUrl(path, task, extra) {
    const base = (window.FRONTEND_API_URL || '').replace(/\/$/, '');
    const url = new URL(base + path, window.location.origin);
    if (task) url.searchParams.set('journal', task);
    Object.entries(extra || {}).forEach(([key, value]) => url.searchParams.set(key, value));
    return base ? url.toString() : url.pathname + url.search;
  }

  function applyPayload(data) {
    if (!data || !data.payload) return;
    if (data.hash && data.hash === lastHash) return;
    if (data.hash) lastHash = data.hash;
    if (window.__AUTORESEARCH_APPLY_PAYLOAD) {
      window.__AUTORESEARCH_APPLY_PAYLOAD(data.payload, data);
    } else {
      window.__AUTORESEARCH_PENDING_PAYLOAD = data.payload;
      window.__AUTORESEARCH_PENDING_DATA = data;
    }
    window.dispatchEvent(new CustomEvent('autoresearch-refresh', { detail: data }));
  }

  async function check(force) {
    if (inFlight) return;
    if (!force && Date.now() - lastFetchAt < 2500) return;
    inFlight = true;
    lastFetchAt = Date.now();
    try {
      const res = await fetch(apiUrl('/api/data', currentTask(), { ts: Date.now() }), { cache: 'no-store' });
      if (!res.ok) return;
      applyPayload(await res.json());
    } catch (_) {
      // Keep the static fallback usable if the dynamic dev server is not running.
    } finally {
      inFlight = false;
    }
  }

  function connectEvents() {
    if (!('EventSource' in window) || params.get('sse') === '0') return;
    const task = currentTask();
    if (events && eventsTask === task) return;
    if (events) events.close();
    try {
      events = new EventSource(apiUrl('/api/events', task));
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
