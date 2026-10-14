export type Bucket = "matched" | "mismatched" | "missing_in_right" | "missing_in_left" | "unknown";

export const BUCKETS: Bucket[] = [
  "matched",
  "mismatched",
  "missing_in_right",
  "missing_in_left",
  "unknown",
];

export interface Summary {
  currency: string;
  left_total: string;
  right_total: string;
  delta: string;
  explained: Partial<Record<Bucket, string>>;
}

export interface Run {
  id: string;
  name: string;
  left_label: string;
  right_label: string;
  left_count: number;
  right_count: number;
  rules: Record<string, unknown>;
  bucket_counts: Record<Bucket, number>;
  summaries: Summary[];
  created_at: string;
}

export interface Item {
  bucket: Bucket;
  match_type: "key" | "fuzzy" | null;
  reasons: string[];
  confidence: number | null;
  delta: string;
  left_id: string | null;
  left_key: string | null;
  left_amount: string | null;
  left_currency: string | null;
  left_at: string | null;
  right_id: string | null;
  right_key: string | null;
  right_amount: string | null;
  right_currency: string | null;
  right_at: string | null;
}

export interface ItemPage {
  total: number;
  items: Item[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body.slice(0, 300)}`);
  }
  return (await res.json()) as T;
}

export const api = {
  runs: () => request<Run[]>("/api/runs"),
  run: (id: string) => request<Run>(`/api/runs/${encodeURIComponent(id)}`),
  items: (id: string, bucket: Bucket | null, offset: number, limit = 50) => {
    const qs = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (bucket) qs.set("bucket", bucket);
    return request<ItemPage>(`/api/runs/${encodeURIComponent(id)}/items?${qs}`);
  },
  demo: () => request<Run>("/api/runs/demo", { method: "POST" }),
  upload: (form: FormData) => request<Run>("/api/runs", { method: "POST", body: form }),
  exportUrl: (id: string) => `/api/runs/${encodeURIComponent(id)}/export.csv`,
};
