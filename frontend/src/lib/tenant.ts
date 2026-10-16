/** Current tenant (one FalkorDB graph each). Lives in the URL (?tenant=) and localStorage; every API call sends it. */
export type TenantInfo = { id: string; company: string; graph: string; default: boolean; loaded: boolean };

const KEY = "pz-tenant";
const DEFAULT = "acme";
const VALID = /^[a-z][a-z0-9_-]{0,31}$/;
let current = DEFAULT;
const listeners = new Set<(t: string) => void>();

/** Read the tenant once on load: URL first, then localStorage, else the default. */
export function initTenant(): string {
  if (typeof window === "undefined") return current;
  try {
    const fromUrl = new URL(window.location.href).searchParams.get("tenant");
    const stored = localStorage.getItem(KEY);
    const t = (fromUrl && VALID.test(fromUrl) ? fromUrl : stored && VALID.test(stored) ? stored : DEFAULT);
    current = t;
    localStorage.setItem(KEY, t);
    syncUrl(t);
  } catch {
    /* storage blocked: keep the default */
  }
  return current;
}

export const getTenant = () => current;

export function setTenant(t: string) {
  if (!VALID.test(t) || t === current) return;
  current = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* ignore */
  }
  syncUrl(t);
  listeners.forEach((f) => f(t));
}

export function onTenant(f: (t: string) => void) {
  listeners.add(f);
  return () => listeners.delete(f);
}

/** The default tenant keeps a clean URL; any other is written as ?tenant=. Other params (run, ...) are kept. */
function syncUrl(t: string) {
  const url = new URL(window.location.href);
  if (t === DEFAULT) url.searchParams.delete("tenant");
  else url.searchParams.set("tenant", t);
  if (url.toString() !== window.location.href) window.history.replaceState(null, "", url.toString());
}

/** Conversation session, one per tenant, kept across reloads. */
export function sessionIdFor(t: string = current): string {
  if (typeof window === "undefined") return "";
  const k = `pz-session-${t}`;
  try {
    let s = localStorage.getItem(k);
    if (!s || !/^[A-Za-z0-9_-]{8,64}$/.test(s)) {
      s = (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, "");
      localStorage.setItem(k, s);
    }
    return s;
  } catch {
    return "";
  }
}

export function newSessionId(t: string = current): string {
  try {
    localStorage.removeItem(`pz-session-${t}`);
  } catch {
    /* ignore */
  }
  return sessionIdFor(t);
}

// A session only exists server-side after its first turn; skip the history fetch (and its 404) until then.
const usedKey = (id: string) => `pz-session-used-${id}`;
export function markSessionUsed(t: string = getTenant()) {
  try { localStorage.setItem(usedKey(sessionIdFor(t)), "1"); } catch { /* storage off */ }
}
export function sessionUsed(t: string = getTenant()): boolean {
  try { return localStorage.getItem(usedKey(sessionIdFor(t))) === "1"; } catch { return true; }
}
/** Drop a session the server no longer knows (after a reset): clear its "used" flag and start a new id. */
export function forgetSession(t: string = getTenant()): string {
  try { localStorage.removeItem(usedKey(sessionIdFor(t))); } catch { /* storage off */ }
  return newSessionId(t);
}
