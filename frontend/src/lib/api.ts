import { getTenant } from "./tenant";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Every call names its tenant: ?tenant= on the URL and an X-Tenant header (the body field, where sent, wins server-side). */
/** The backend reports a FalkorDB outage as database_unavailable (older builds: graph_unavailable). */
export const isDbDown = (kind?: string | null) => kind === "database_unavailable" || kind === "graph_unavailable";

export function withTenant(path: string, tenant = getTenant()): string {
  if (/[?&]tenant=/.test(path)) return path; // an explicit tenant wins
  return `${path}${path.includes("?") ? "&" : "?"}tenant=${encodeURIComponent(tenant)}`;
}

/** Admin token for guarded endpoints (reset, cleanup, DELETE). Set once with ?admin=<token>; kept for the tab session only. */
const ADMIN_KEY = "pz-admin-token";
export function adminToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    const url = new URL(window.location.href);
    const t = url.searchParams.get("admin");
    if (t) {
      sessionStorage.setItem(ADMIN_KEY, t);
      url.searchParams.delete("admin"); // keep the token out of the address bar and history
      window.history.replaceState(null, "", url.toString());
    }
    return sessionStorage.getItem(ADMIN_KEY);
  } catch {
    return null;
  }
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public kind: string = "error",
  ) {
    super(message);
  }
}

export async function handle<T>(res: Response): Promise<T> {
  if (res.ok) return res.json() as Promise<T>;
  let msg = `${res.status} ${res.statusText}`;
  let kind = "error";
  try {
    const body = await res.json();
    if (body?.message) msg = body.message;
    else if (typeof body?.detail === "string") msg = body.detail;
    else if (Array.isArray(body?.detail)) msg = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    if (body?.error) kind = body.error;
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(msg, res.status, kind);
}

export type GetOptions = { timeoutMs?: number; retries?: number };

/** GET with an optional per-attempt timeout and retries. A retry only follows a timeout, a network error or a 5xx
 *  other than database_unavailable (a real outage is reported at once, not hidden behind a second wait). */
export async function apiGet<T>(path: string, tenant: string = getTenant(), opts: GetOptions = {}): Promise<T> {
  const { timeoutMs, retries = 0 } = opts;
  for (let attempt = 0; ; attempt++) {
    const last = attempt >= retries;
    const ctl = timeoutMs ? new AbortController() : null;
    const timer = ctl ? setTimeout(() => ctl.abort(), timeoutMs) : null;
    let res: Response;
    try {
      res = await fetch(`${API_URL}${withTenant(path, tenant)}`, { cache: "no-store", headers: { "X-Tenant": tenant }, signal: ctl?.signal });
    } catch {
      if (timer) clearTimeout(timer);
      if (!last) continue;
      if (ctl?.signal.aborted) throw new ApiError(`The API did not answer within ${Math.round((timeoutMs ?? 0) / 1000)} s`, 0, "timeout");
      throw new ApiError(`API unreachable at ${API_URL}. Is the backend running?`, 0, "network");
    }
    try {
      return await handle<T>(res);
    } catch (e) {
      const ae = e as ApiError;
      if (!last && ae.status >= 500 && !isDbDown(ae.kind)) continue;
      throw e;
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
}

/** Startup loads (health, fleet graph, incidents): about 8 s per attempt, one retry. */
export const LOAD_OPTS: GetOptions = { timeoutMs: 8000, retries: 1 };

export async function apiPost<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  let res: Response;
  const headers: Record<string, string> = { "content-type": "application/json", "X-Tenant": getTenant() };
  const admin = adminToken();
  if (admin) headers["X-Admin-Token"] = admin;
  try {
    res = await fetch(`${API_URL}${withTenant(path)}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e; // superseded by a newer request: the caller drops it

    throw new ApiError(`API unreachable at ${API_URL}. Is the backend running?`, 0, "network");
  }
  return handle<T>(res);
}
