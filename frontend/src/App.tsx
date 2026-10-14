import { useCallback, useEffect, useState } from "react";
import { api, type Run } from "./api";
import { RunDetail } from "./RunDetail";
import { UploadForm } from "./UploadForm";
import { money } from "./format";

function useHashRoute(): [string | null, (id: string | null) => void] {
  const read = () => {
    const match = window.location.hash.match(/^#\/runs\/(.+)$/);
    return match?.[1] ? decodeURIComponent(match[1]) : null;
  };
  const [runId, setRunId] = useState(read);
  useEffect(() => {
    const onChange = () => setRunId(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  const go = (id: string | null) => {
    window.location.hash = id ? `/runs/${encodeURIComponent(id)}` : "";
  };
  return [runId, go];
}

export function App() {
  const [runId, go] = useHashRoute();
  return (
    <div className="shell">
      <header className="topbar">
        <a href="#" className="brand" onClick={() => go(null)}>
          reconcile
        </a>
        <span className="tagline">Do system A and system B agree? If not, exactly where not.</span>
      </header>
      <main>{runId ? <RunDetail runId={runId} /> : <RunList onOpen={go} />}</main>
    </div>
  );
}

function RunList({ onOpen }: { onOpen: (id: string) => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.runs().then(setRuns, (e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  const runDemo = async () => {
    setBusy(true);
    setError(null);
    try {
      onOpen((await api.demo()).id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="panel hero">
        <div>
          <h1>Reconciliation runs</h1>
          <p className="muted">
            Load a demo month of Stripe charges against ERP orders with planted discrepancies, or
            upload two CSV exports of your own.
          </p>
        </div>
        <button className="primary" onClick={runDemo} disabled={busy}>
          {busy ? "Reconciling…" : "Run the demo"}
        </button>
      </section>

      {error && <p className="error">{error}</p>}

      <section className="panel">
        <h2>Recent runs</h2>
        {runs === null ? (
          <p className="muted">Loading…</p>
        ) : runs.length === 0 ? (
          <p className="muted">No runs yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Records</th>
                  <th>Matched</th>
                  <th>Needs attention</th>
                  <th>Difference</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const total = Object.values(r.bucket_counts).reduce((a, b) => a + b, 0);
                  const attention = total - r.bucket_counts.matched;
                  return (
                    <tr key={r.id} className="clickable" onClick={() => onOpen(r.id)}>
                      <td>
                        <strong>{r.name}</strong>
                        <div className="muted small">{new Date(r.created_at).toLocaleString()}</div>
                      </td>
                      <td className="num">
                        {r.left_count} / {r.right_count}
                      </td>
                      <td className="num">{r.bucket_counts.matched}</td>
                      <td className="num">{attention}</td>
                      <td className="num">
                        {r.summaries.map((s) => (
                          <div key={s.currency}>{money(s.delta, s.currency)}</div>
                        ))}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <UploadForm onDone={(run) => onOpen(run.id)} />
    </>
  );
}
