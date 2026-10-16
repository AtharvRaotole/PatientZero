export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public kind: string = "error",
  ) {
    super(message);
  }
}

async function handle<T>(res: Response): Promise<T> {
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

export async function apiGet<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  } catch {
    throw new ApiError(`API unreachable at ${API_URL}. Is the backend running?`, 0, "network");
  }
  return handle<T>(res);
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(`API unreachable at ${API_URL}. Is the backend running?`, 0, "network");
  }
  return handle<T>(res);
}
