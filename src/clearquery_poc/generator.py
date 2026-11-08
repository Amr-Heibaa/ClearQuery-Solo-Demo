"""Deterministic synthetic Sales data generator for the ClearQuery 10% demo.

Approved decisions: B4 (synthetic data) + Phase 2a final decisions.

* Python standard library only; a private ``random.Random(SEED)`` instance
  (the global ``random`` module state is never used).
* Amounts are integer piasters converted to ``Decimal`` (no float error).
* Order counts per branch/month are fixed quotas; randomness is limited to
  day-of-month and amount, so the business guarantees are by construction
  and are then re-validated (generation fails loudly if any check fails;
  it never silently re-samples).
* All branches and orders are fictitious. No real people or companies.

Usage::

    python -m clearquery_poc.generator --out data
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

SEED = 20261101
AS_OF_DATE = date(2026, 11, 1)
CURRENCY = "EGP"
EXPECTED_BRANCHES = 4
EXPECTED_ORDERS = 50


@dataclass(frozen=True)
class Branch:
    branch_id: int
    branch_name: str
    branch_name_ar: str
    amount_min_egp: int
    amount_max_egp: int


@dataclass(frozen=True)
class Order:
    order_id: int
    branch_id: int
    order_date: date
    total_amount: Decimal


BRANCHES: tuple[Branch, ...] = (
    Branch(1, "Nasr City", "مدينة نصر", 150, 1_500),
    Branch(2, "Heliopolis", "مصر الجديدة", 300, 3_000),
    Branch(3, "Maadi", "المعادي", 2_000, 8_000),
    Branch(4, "Alexandria", "إسكندرية", 500, 4_000),
)

# (year, month) -> {branch_id: order count}. Month totals: Aug 8, Sep 15,
# Oct 26, Nov 1 (= 50). Alexandria has no August orders (tests LEFT JOIN).
QUOTAS: dict[tuple[int, int], dict[int, int]] = {
    (2026, 8): {1: 3, 2: 3, 3: 2, 4: 0},
    (2026, 9): {1: 4, 2: 4, 3: 3, 4: 4},
    (2026, 10): {1: 9, 2: 7, 3: 5, 4: 5},
    (2026, 11): {1: 0, 2: 1, 3: 0, 4: 0},
}

# Explicit boundary records: (branch_id, month key, fixed date). Each one
# consumes one slot of that branch/month quota.
BOUNDARY_ORDERS: tuple[tuple[int, tuple[int, int], date], ...] = (
    (4, (2026, 9), date(2026, 9, 30)),    # last day before October
    (1, (2026, 10), date(2026, 10, 1)),   # first day of October
    (3, (2026, 10), date(2026, 10, 31)),  # last day of October
    (2, (2026, 11), date(2026, 11, 1)),   # = as_of_date; NOT in October
)


def generate(seed: int = SEED) -> tuple[list[Branch], list[Order]]:
    """Return (branches, orders). Deterministic for a given seed."""
    rng = random.Random(seed)
    by_id = {b.branch_id: b for b in BRANCHES}
    fixed: dict[tuple[int, tuple[int, int]], list[date]] = {}
    for branch_id, month, d in BOUNDARY_ORDERS:
        fixed.setdefault((branch_id, month), []).append(d)

    raw: list[tuple[date, int, Decimal]] = []
    for (year, month), per_branch in sorted(QUOTAS.items()):
        last_day = calendar.monthrange(year, month)[1]
        if (year, month) == (AS_OF_DATE.year, AS_OF_DATE.month):
            last_day = AS_OF_DATE.day  # nothing after as_of_date
        for branch_id in sorted(per_branch):
            n = per_branch[branch_id]
            dates = list(fixed.get((branch_id, (year, month)), []))
            if len(dates) > n:
                raise ValueError(f"boundary rows exceed quota for {branch_id} {year}-{month}")
            while len(dates) < n:
                dates.append(date(year, month, rng.randint(1, last_day)))
            b = by_id[branch_id]
            for d in dates:
                piasters = rng.randint(b.amount_min_egp * 100, b.amount_max_egp * 100)
                raw.append((d, branch_id, Decimal(piasters).scaleb(-2)))

    raw.sort(key=lambda r: (r[0], r[1], r[2]))
    orders = [Order(i, bid, d, amt) for i, (d, bid, amt) in enumerate(raw, start=1)]
    branches = list(BRANCHES)
    validate(branches, orders)
    return branches, orders


# ---------------------------------------------------------------- validation

def month_range(year: int, month: int) -> tuple[date, date]:
    """Half-open [first day, first day of next month)."""
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1)
    return start, end


def metrics(orders: list[Order], start: date, end: date) -> dict[int, tuple[int, Decimal]]:
    """branch_id -> (order_count, total_sales) for start <= order_date < end."""
    out = {b.branch_id: (0, Decimal("0.00")) for b in BRANCHES}
    for o in orders:
        if start <= o.order_date < end:
            c, s = out[o.branch_id]
            out[o.branch_id] = (c + 1, s + o.total_amount)
    return out


def validate(branches: list[Branch], orders: list[Order]) -> None:
    """Raise AssertionError if any approved guarantee does not hold."""
    ids = [b.branch_id for b in branches]
    assert len(branches) == EXPECTED_BRANCHES, len(branches)
    assert len(set(ids)) == len(ids)
    assert len(orders) == EXPECTED_ORDERS, len(orders)
    assert len({o.order_id for o in orders}) == len(orders)
    for o in orders:
        assert o.branch_id in ids, o
        assert o.total_amount > 0, o
        assert o.total_amount == o.total_amount.quantize(Decimal("0.01")), o
        assert date(2026, 8, 1) <= o.order_date <= AS_OF_DATE, o
    month_counts = {m: sum(1 for o in orders if (o.order_date.year, o.order_date.month) == m)
                    for m in QUOTAS}
    assert month_counts == {(2026, 8): 8, (2026, 9): 15, (2026, 10): 26, (2026, 11): 1}, month_counts
    nov = [o for o in orders if o.order_date.month == 11]
    assert [o.order_date for o in nov] == [AS_OF_DATE]
    for d in (date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 1)):
        assert any(o.order_date == d for o in orders), f"missing boundary row {d}"

    oct_m = metrics(orders, *month_range(2026, 10))
    top_count = max(oct_m.values(), key=lambda v: v[0])[0]
    top_sales = max(oct_m.values(), key=lambda v: v[1])[1]
    count_leaders = [bid for bid, v in oct_m.items() if v[0] == top_count]
    sales_leaders = [bid for bid, v in oct_m.items() if v[1] == top_sales]
    assert count_leaders == [1], f"Nasr City must uniquely lead Oct order_count: {oct_m}"
    assert sales_leaders == [3], f"Maadi must uniquely lead Oct total_sales: {oct_m}"
    assert count_leaders != sales_leaders
    assert all(v[0] >= 1 for v in oct_m.values()), "every branch needs October orders"


# ------------------------------------------------------------------- output

def _csv(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def render(branches: list[Branch], orders: list[Order]) -> dict[str, bytes]:
    """Return {filename: bytes}. Byte-identical for identical input."""
    files: dict[str, bytes] = {}
    files["branches.csv"] = _csv(
        [["branch_id", "branch_name", "branch_name_ar"]]
        + [[str(b.branch_id), b.branch_name, b.branch_name_ar] for b in branches])
    files["orders.csv"] = _csv(
        [["order_id", "branch_id", "order_date", "total_amount"]]
        + [[str(o.order_id), str(o.branch_id), o.order_date.isoformat(), str(o.total_amount)]
           for o in orders])

    lines = [
        "-- GENERATED by clearquery_poc.generator — do not edit by hand.",
        f"-- SYNTHETIC data only. seed={SEED} currency={CURRENCY} as_of_date={AS_OF_DATE}",
        "BEGIN;",
        "INSERT INTO sales_poc.branches (branch_id, branch_name, branch_name_ar) VALUES",
        ",\n".join(f"  ({b.branch_id}, {_sql_str(b.branch_name)}, {_sql_str(b.branch_name_ar)})"
                   for b in branches) + ";",
        "INSERT INTO sales_poc.orders (order_id, branch_id, order_date, total_amount) VALUES",
        ",\n".join(f"  ({o.order_id}, {o.branch_id}, DATE '{o.order_date.isoformat()}', {o.total_amount})"
                   for o in orders) + ";",
        "COMMIT;",
        "",
    ]
    files["seed.sql"] = "\n".join(lines).encode("utf-8")

    catalog = {
        "note": "SYNTHETIC. Branch aliases for Person 3 (BIR/catalog). Canonical SQL literal = branch_name.",
        "as_of_date": AS_OF_DATE.isoformat(),
        "currency": CURRENCY,
        "branches": [{"branch_id": b.branch_id, "branch_name": b.branch_name,
                      "branch_name_ar": b.branch_name_ar} for b in branches],
    }
    files["branch_catalog.json"] = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    sums = "".join(f"{hashlib.sha256(files[n]).hexdigest()}  {n}\n" for n in sorted(files))
    files["SHA256SUMS"] = sums.encode("utf-8")
    return files


def write(out_dir: Path, seed: int = SEED) -> dict[str, bytes]:
    branches, orders = generate(seed)
    files = render(branches, orders)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
    return files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("data"))
    args = ap.parse_args()
    files = write(args.out)
    print(f"wrote {len(files)} files to {args.out.resolve()} (seed={SEED})")


if __name__ == "__main__":
    main()
