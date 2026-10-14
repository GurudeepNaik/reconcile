import { useState, type FormEvent } from "react";
import { api, type Run } from "./api";

const DEFAULT_FIELDS = JSON.stringify(
  {
    id: "id",
    key: "order_ref",
    amount: "amount",
    currency: "currency",
    occurred_at: "created",
    timezone: "UTC",
  },
  null,
  2,
);

export function UploadForm({ onDone }: { onDone: (run: Run) => void }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const form = new FormData(e.currentTarget);
      for (const field of ["left_fields", "right_fields", "rules"]) {
        JSON.parse(String(form.get(field)));
      }
      onDone(await api.upload(form));
    } catch (err) {
      setError(err instanceof SyntaxError ? `Invalid JSON: ${err.message}` : (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel">
      <h2>Reconcile your own exports</h2>
      <form className="upload" onSubmit={submit}>
        <label className="wide">
          Run name
          <input name="name" defaultValue="My reconciliation" maxLength={200} />
        </label>
        {(["left", "right"] as const).map((side) => (
          <fieldset key={side}>
            <legend>{side === "left" ? "Left system" : "Right system"}</legend>
            <label>
              Label
              <input name={`${side}_label`} defaultValue={side === "left" ? "Stripe" : "ERP"} />
            </label>
            <label>
              CSV file
              <input type="file" name={`${side}_file`} accept=".csv,text/csv" required />
            </label>
            <label>
              Column mapping (JSON)
              <textarea name={`${side}_fields`} rows={8} defaultValue={DEFAULT_FIELDS} spellCheck={false} />
            </label>
          </fieldset>
        ))}
        <label className="wide">
          Rules (JSON)
          <textarea
            name="rules"
            rows={3}
            spellCheck={false}
            defaultValue={JSON.stringify({ amount_tolerance: "0.00", date_window_hours: 48 })}
          />
        </label>
        <div className="wide">
          <button className="primary" disabled={busy}>
            {busy ? "Reconciling…" : "Reconcile"}
          </button>
          {error && <p className="error">{error}</p>}
        </div>
      </form>
    </section>
  );
}
