(function () {
  const params = new URLSearchParams(window.location.search);
  if (params.get('live') === '0') return;

  if ('EventSource' in window && params.get('sse') !== '0') {
    const source = new EventSource('/api/dev-events');
    source.addEventListener('reload', () => window.location.reload());
    source.onerror = () => {};
    return;
  }

  let currentHash = null;
  let currentFrames = null;
  async function check() {
    try {
      const res = await fetch('/api/changelog?ts=' + Date.now(), { cache: 'no-store' });
      if (!res.ok) return;
      const meta = await res.json();
      const frames = meta.frames || [];
      const last = frames[frames.length - 1];
      if (!last || !last.hash) return;
      if (currentHash == null) {
        currentHash = last.hash;
        currentFrames = frames.length;
        return;
      }
      if (last.hash !== currentHash || frames.length !== currentFrames) {
        window.location.reload();
      }
    } catch (_) {
      // Keep the static fallback usable if the dynamic dev server is not running.
    }
  }

  check();
  setInterval(check, 3000);
})();
