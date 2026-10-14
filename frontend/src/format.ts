import type { Bucket } from "./api";

export const BUCKET_LABEL: Record<Bucket, string> = {
  matched: "Matched",
  mismatched: "Mismatched",
  missing_in_right: "Missing on right",
  missing_in_left: "Missing on left",
  unknown: "Needs review",
};

export const REASON_LABEL: Record<string, string> = {
  amount_differs: "amount differs",
  currency_differs: "currency differs",
  date_outside_window: "date outside window",
  duplicate_key: "duplicate key",
  ambiguous_candidates: "several equally good candidates",
  invalid_record: "unparseable record",
};

export function bucketLabel(bucket: Bucket, left: string, right: string): string {
  if (bucket === "missing_in_right") return `Only in ${left}`;
  if (bucket === "missing_in_left") return `Only in ${right}`;
  return BUCKET_LABEL[bucket];
}

export function money(value: string | null, currency?: string | null): string {
  if (value === null) return "—";
  const n = Number(value);
  const formatted = n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return currency && currency !== "???" ? `${formatted} ${currency}` : formatted;
}

export function signed(value: string): string {
  const n = Number(value);
  const abs = Math.abs(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return n > 0 ? `+${abs}` : n < 0 ? `−${abs}` : abs;
}

export function when(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  });
}
