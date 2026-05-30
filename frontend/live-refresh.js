(function () {
  const params = new URLSearchParams(window.location.search);
  if (params.get('live') === '0') return;

  if ('EventSource' in window && params.get('sse') !== '0') {
    const source = new EventSource('/api/dev-events');
    source.addEventListener('reload', () => window.location.reload());
    source.onerror = () => {};
    return;
  }

  let inFlight = false;
  async function check() {
    if (inFlight) return;
    inFlight = true;
    try {
      const res = await fetch('/api/data?ts=' + Date.now(), { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.payload) {
        if (window.__AUTORESEARCH_APPLY_PAYLOAD) {
          window.__AUTORESEARCH_APPLY_PAYLOAD(data.payload);
        } else {
          window.__AUTORESEARCH_PENDING_PAYLOAD = data.payload;
        }
      }
    } catch (_) {
      // Keep the static fallback usable if the dynamic dev server is not running.
    } finally {
      inFlight = false;
    }
  }

  check();
  setInterval(check, 3000);
})();
