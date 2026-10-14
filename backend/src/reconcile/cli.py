"""Command line entry point. `reconcile run job.yaml --strict` exits 2 when anything is unexplained,
so it can gate a nightly job or a month-end close checklist."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Annotated

import typer

from reconcile import demo as demo_data
from reconcile.config import build_source, load_job
from reconcile.domain import Bucket, Result, Side
from reconcile.engine import reconcile

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command()
def run(
    job_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    out: Annotated[Path | None, typer.Option(help="Write every outcome to this CSV")] = None,
    strict: Annotated[bool, typer.Option(help="Exit 2 unless every record matched")] = False,
) -> None:
    """Run a reconciliation described by a YAML job file."""
    job = load_job(job_file)
    left = build_source(job.left).fetch(Side.LEFT)
    right = build_source(job.right).fetch(Side.RIGHT)
    result = reconcile(left, right, job.rules.to_rules())
    _print(result, job.name, job.left_label, job.right_label)
    if out:
        _write(result, out)
        typer.echo(f"\nwrote {len(result.outcomes)} outcomes to {out}")
    unmatched = sum(1 for o in result.outcomes if o.bucket is not Bucket.MATCHED)
    if strict and unmatched:
        raise typer.Exit(code=2)


@app.command()
def demo(
    directory: Annotated[Path, typer.Option("--dir")] = Path("demo-data"),
    seed: int = 7,
) -> None:
    """Write the demo Stripe/ERP CSVs and a job file that reconciles them."""
    data = demo_data.generate(seed=seed)
    stripe_csv, erp_csv = data.write(directory)
    job = directory / "job.yaml"
    job.write_text(
        f"""name: Stripe vs ERP, September 2026
left_label: Stripe
right_label: ERP
left:
  type: csv
  path: {stripe_csv.name}
  fields: {demo_data.STRIPE_FIELDS.model_dump_json(exclude_defaults=True)}
right:
  type: csv
  path: {erp_csv.name}
  fields: {demo_data.ERP_FIELDS.model_dump_json(exclude_defaults=True)}
rules:
  amount_tolerance: "0.00"
  date_window_hours: 48
"""
    )
    typer.echo(f"wrote {stripe_csv}, {erp_csv}, {job}\nnext: reconcile run {job}")


def _print(result: Result, name: str, left_label: str, right_label: str) -> None:
    groups = result.by_bucket()
    typer.echo(
        f"{name}\n{left_label}: {result.left_count} records   {right_label}: {result.right_count}"
    )
    typer.echo("")
    for bucket in Bucket:
        typer.echo(f"  {bucket.value:<18}{len(groups[bucket]):>7}")
    for s in result.summaries:
        typer.echo(
            f"\n{s.currency}: {left_label} {s.left_total:,.2f}  {right_label} {s.right_total:,.2f}"
            f"  difference {s.delta:,.2f}"
        )
        for bucket, amount in sorted(s.explained.items(), key=lambda kv: -abs(kv[1])):
            typer.echo(f"  {bucket.value:<18}{amount:>14,.2f}")


def _write(result: Result, path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "bucket",
                "match_type",
                "reasons",
                "confidence",
                "delta",
                "left_id",
                "left_key",
                "left_amount",
                "left_currency",
                "left_at",
                "right_id",
                "right_key",
                "right_amount",
                "right_currency",
                "right_at",
            ]
        )
        for o in result.outcomes:
            row: list[object] = [
                o.bucket.value,
                o.match_type.value if o.match_type else "",
                ";".join(r.value for r in o.reasons),
                o.confidence if o.confidence is not None else "",
                o.delta,
            ]
            for rec in (o.left, o.right):
                row += (
                    [rec.source_id, rec.key, rec.amount, rec.currency, rec.occurred_at]
                    if rec
                    else ["", "", "", "", ""]
                )
            writer.writerow(row)


if __name__ == "__main__":
    app()
