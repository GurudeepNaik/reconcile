import { useEffect, useState } from "react";
import { api, BUCKETS, type Bucket, type ItemPage, type Run, type Summary } from "./api";
import { REASON_LABEL, bucketLabel, money, signed, when } from "./format";

const PAGE = 50;

export function RunDetail({ runId }: { runId: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [bucket, setBucket] = useState<Bucket | null>(null);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<ItemPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.run(runId).then(setRun, (e: Error) => setError(e.message));
  }, [runId]);

  useEffect(() => {
    setPage(null);
    api.items(runId, bucket, offset, PAGE).then(setPage, (e: Error) => setError(e.message));
  }, [runId, bucket, offset]);

  if (error) return <p className="error">{error}</p>;
  if (!run) return <p className="muted">Loading…</p>;

  const total = run.left_count + run.right_count;
  const pick = (b: Bucket | null) => {
    setBucket(b);
    setOffset(0);
  };

  return (
    <>
      <section className="panel">
        <div className="run-head">
          <div>
            <h1>{run.name}</h1>
            <p className="muted">
              {run.left_label}: {run.left_count} records · {run.right_label}: {run.right_count}{" "}
              records · amount tolerance {String(run.rules.amount_tolerance)} · date window{" "}
              {String(run.rules.date_window_hours)}h
            </p>
          </div>
          <a className="button" href={api.exportUrl(run.id)}>
            Export CSV
          </a>
        </div>

        <div className="cards">
          {BUCKETS.map((b) => (
            <button
              key={b}
              className={`card bucket-${b} ${bucket === b ? "active" : ""}`}
              onClick={() => pick(bucket === b ? null : b)}
            >
              <span className="card-label">{bucketLabel(b, run.left_label, run.right_label)}</span>
              <span className="card-value">{run.bucket_counts[b]}</span>
            </button>
          ))}
        </div>
        <p className="muted small">
          {total} input records, every one placed in exactly one bucket. Click a bucket to filter.
        </p>
      </section>

      {run.summaries.map((s) => (
        <DeltaExplainer key={s.currency} summary={s} run={run} />
      ))}

      <section className="panel">
        <h2>
          {bucket ? bucketLabel(bucket, run.left_label, run.right_label) : "All outcomes"}
          <span className="muted small"> · largest differences first</span>
        </h2>
        <ItemsTable run={run} page={page} />
        {page && page.total > PAGE && (
          <div className="pager">
            <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
              Previous
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, page.total)} of {page.total}
            </span>
            <button
              disabled={offset + PAGE >= page.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </button>
          </div>
        )}
      </section>
    </>
  );
}

function DeltaExplainer({ summary: s, run }: { summary: Summary; run: Run }) {
  const parts = BUCKETS.filter((b) => s.explained[b] !== undefined).sort(
    (a, b) => Math.abs(Number(s.explained[b])) - Math.abs(Number(s.explained[a])),
  );
  const sum = parts.reduce((acc, b) => acc + Math.round(Number(s.explained[b]) * 100), 0);
  const balanced = sum === Math.round(Number(s.delta) * 100);
  const max = Math.max(...parts.map((b) => Math.abs(Number(s.explained[b]))), 0.01);

  return (
    <section className="panel">
      <h2>Where the {s.currency} difference comes from</h2>
      <div className="totals">
        <div>
          <span className="muted small">{run.left_label}</span>
          <strong>{money(s.left_total, s.currency)}</strong>
        </div>
        <div>
          <span className="muted small">{run.right_label}</span>
          <strong>{money(s.right_total, s.currency)}</strong>
        </div>
        <div>
          <span className="muted small">Difference</span>
          <strong>{signed(s.delta)}</strong>
        </div>
      </div>
      {parts.length === 0 ? (
        <p className="muted">The totals agree exactly.</p>
      ) : (
        <table className="explain">
          <tbody>
            {parts.map((b) => {
              const v = Number(s.explained[b]);
              return (
                <tr key={b}>
                  <td>{bucketLabel(b, run.left_label, run.right_label)}</td>
                  <td className="bar-cell">
                    <span
                      className={`bar ${v < 0 ? "neg" : "pos"}`}
                      style={{ width: `${(Math.abs(v) / max) * 100}%` }}
                    />
                  </td>
                  <td className="num">{signed(s.explained[b]!)}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <td>Total</td>
              <td className={balanced ? "ok" : "error"}>
                {balanced ? "✓ explains the whole difference" : "does not balance"}
              </td>
              <td className="num">{signed(s.delta)}</td>
            </tr>
          </tfoot>
        </table>
      )}
    </section>
  );
}

function ItemsTable({ run, page }: { run: Run; page: ItemPage | null }) {
  if (!page) return <p className="muted">Loading…</p>;
  if (page.items.length === 0) return <p className="muted">Nothing here.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Outcome</th>
            <th>{run.left_label}</th>
            <th>{run.right_label}</th>
            <th className="num">Difference</th>
          </tr>
        </thead>
        <tbody>
          {page.items.map((it, i) => (
            <tr key={`${it.left_id}-${it.right_id}-${i}`}>
              <td>
                <span className={`pill bucket-${it.bucket}`}>
                  {bucketLabel(it.bucket, run.left_label, run.right_label)}
                </span>
                {it.match_type === "fuzzy" && (
                  <div className="muted small">
                    fuzzy match · {Math.round((it.confidence ?? 0) * 100)}% confidence
                  </div>
                )}
                {it.reasons.length > 0 && (
                  <div className="muted small">
                    {it.reasons.map((r) => REASON_LABEL[r] ?? r).join(", ")}
                  </div>
                )}
              </td>
              <SideCell id={it.left_id} k={it.left_key} amount={it.left_amount} cur={it.left_currency} at={it.left_at} />
              <SideCell id={it.right_id} k={it.right_key} amount={it.right_amount} cur={it.right_currency} at={it.right_at} />
              <td className="num">{signed(it.delta)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SideCell(p: {
  id: string | null;
  k: string | null;
  amount: string | null;
  cur: string | null;
  at: string | null;
}) {
  if (!p.id) return <td className="muted">—</td>;
  return (
    <td>
      <div>
        <code>{p.id}</code>
        {p.k && <span className="muted small"> · {p.k}</span>}
      </div>
      <div className="small">
        {money(p.amount, p.cur)} <span className="muted">· {when(p.at)}</span>
      </div>
    </td>
  );
}
