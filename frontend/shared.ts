type QueryParams = Record<string, string | number | boolean>;

function fmt(value: unknown): string {
  if (value == null) return '—';
  if (typeof value !== 'number') return String(value);
  if (!Number.isFinite(value)) return String(value);
  const abs = Math.abs(value);
  const maximumFractionDigits = Number.isInteger(value) ? 0 : abs >= 100 ? 2 : abs >= 1 ? 3 : 4;
  return value.toLocaleString(undefined, { maximumFractionDigits });
}

function mmss(value: unknown): string {
  const safe = Math.max(0, typeof value === 'number' && Number.isFinite(value) ? value : 0);
  const total = Math.round(safe);
  return String(Math.floor(total / 60)).padStart(2, '0') + ':' + String(total % 60).padStart(2, '0');
}

function queryParam(name: string): string {
  return new URLSearchParams(window.location.search).get(name) || '';
}

function currentTask(): string {
  return window.__AUTORESEARCH_JOURNAL || queryParam('journal') || localStorage.getItem('autoresearch-task') || '';
}

function apiUrl(path: string, task?: string, params?: QueryParams): string {
  const base = (window.FRONTEND_API_URL || '').replace(/\/$/, '');
  const url = new URL(base + path, window.location.origin);
  if (task) url.searchParams.set('journal', task);
  Object.entries(params || {}).forEach(([key, value]) => url.searchParams.set(key, String(value)));
  return base ? url.toString() : url.pathname + url.search;
}

window.AutoresearchUI = { apiUrl, currentTask, fmt, mmss, queryParam };
