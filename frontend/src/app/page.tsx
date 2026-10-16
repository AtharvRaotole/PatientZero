"use client";

import { useCallback, useEffect, useState } from "react";
import { API_URL, apiGet } from "@/lib/api";

type Health = { status: string; falkordb: string; graphs?: string[]; error?: string };
type Demo = {
  nodes: { id: number; label: string; name: string }[];
  edges: { source: number; target: number; type: string }[];
};

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [demo, setDemo] = useState<Demo | null>(null);

  const check = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setHealth(await apiGet<Health>("/health"));
    } catch (e) {
      setHealth(null);
      setError(`Backend unreachable at ${API_URL} (${(e as Error).message})`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  const loadDemo = async () => {
    try {
      setDemo(await apiGet<Demo>("/api/graph/demo"));
    } catch (e) {
      setError(`Demo graph failed: ${(e as Error).message}`);
    }
  };

  const ok = health?.status === "ok";
  const name = (id: number) => demo?.nodes.find((n) => n.id === id)?.name ?? id;

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-8">
      <div className="mx-auto max-w-2xl space-y-6">
        <h1 className="text-3xl font-bold">PatientZero</h1>
        <p className="text-slate-400">Graph-powered outbreak tracing on FalkorDB.</p>

        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">System status</h2>
            <button onClick={check} className="rounded bg-slate-700 px-3 py-1 text-sm hover:bg-slate-600">
              Recheck
            </button>
          </div>
          {loading ? (
            <p className="text-slate-400" data-testid="status">Checking…</p>
          ) : error ? (
            <p className="text-red-400" data-testid="status">{error}</p>
          ) : (
            <div data-testid="status" className="space-y-1 text-sm">
              <p>
                Backend: <span className="text-green-400">up</span>
              </p>
              <p>
                FalkorDB:{" "}
                <span className={ok ? "text-green-400" : "text-red-400"}>{health?.falkordb}</span>
                {health?.error && <span className="text-red-400"> — {health.error}</span>}
              </p>
              {health?.graphs && <p className="text-slate-400">Graphs: {health.graphs.join(", ") || "none"}</p>}
            </div>
          )}
        </section>

        <section className="rounded-xl border border-slate-800 bg-slate-900 p-5 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Sample graph query</h2>
            <button
              onClick={loadDemo}
              disabled={!ok}
              className="rounded bg-indigo-600 px-3 py-1 text-sm hover:bg-indigo-500 disabled:opacity-40"
            >
              Run demo query
            </button>
          </div>
          {demo ? (
            <ul className="text-sm space-y-1">
              {demo.edges.map((e, i) => (
                <li key={i}>
                  {name(e.source)} <span className="text-indigo-400">-[{e.type}]→</span> {name(e.target)}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-500">No results yet.</p>
          )}
        </section>
      </div>
    </main>
  );
}
