"""Paper trade logging — CSV-backed ledger."""

import csv
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


TRADES_FILE = Path(__file__).parent.parent / "data" / "trades" / "paper_trades.csv"

FIELDNAMES = [
    "id",
    "opened_at",
    "closed_at",
    "status",          # open | closed | expired
    "strategy",
    "underlying",
    "legs",            # JSON string describing legs
    "entry_debit_credit",   # positive = debit paid, negative = credit received
    "exit_debit_credit",    # positive = paid to close, negative = received
    "pnl",
    "notes",
    # Greeks at entry
    "entry_delta",
    "entry_gamma",
    "entry_theta",
    "entry_vega",
    "entry_iv",
]


def _ensure_file():
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not TRADES_FILE.exists():
        with open(TRADES_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()


def open_trade(
    strategy: str,
    underlying: str,
    legs: str,                  # JSON or human-readable description
    entry_debit_credit: float,  # debit = positive, credit = negative
    entry_delta: Optional[float] = None,
    entry_gamma: Optional[float] = None,
    entry_theta: Optional[float] = None,
    entry_vega: Optional[float] = None,
    entry_iv: Optional[float] = None,
    notes: str = "",
) -> str:
    """Log a new paper trade. Returns the trade ID."""
    _ensure_file()
    trade_id = str(uuid.uuid4())[:8]
    row = {
        "id": trade_id,
        "opened_at": datetime.now().isoformat(timespec="seconds"),
        "closed_at": "",
        "status": "open",
        "strategy": strategy,
        "underlying": underlying,
        "legs": legs,
        "entry_debit_credit": entry_debit_credit,
        "exit_debit_credit": "",
        "pnl": "",
        "notes": notes,
        "entry_delta": entry_delta or "",
        "entry_gamma": entry_gamma or "",
        "entry_theta": entry_theta or "",
        "entry_vega": entry_vega or "",
        "entry_iv": entry_iv or "",
    }
    with open(TRADES_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writerow(row)
    return trade_id


def close_trade(trade_id: str, exit_debit_credit: float, notes: str = "") -> dict:
    """Mark a trade closed and calculate P&L. Returns the updated row."""
    _ensure_file()
    rows = []
    updated = None

    with open(TRADES_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["id"] == trade_id:
                if row["status"] != "open":
                    raise ValueError(f"Trade {trade_id} is already {row['status']}")
                entry = float(row["entry_debit_credit"])
                # P&L: for a debit spread, pnl = entry_cost - exit_cost (pay less = profit)
                # For a credit spread, pnl = credit_received - exit_cost
                pnl = -entry - exit_debit_credit  # sign convention: negative entry = credit received
                row.update(
                    {
                        "closed_at": datetime.now().isoformat(timespec="seconds"),
                        "status": "closed",
                        "exit_debit_credit": exit_debit_credit,
                        "pnl": round(pnl, 2),
                        "notes": (row["notes"] + " | " + notes).strip(" |") if notes else row["notes"],
                    }
                )
                updated = row
            rows.append(row)

    if updated is None:
        raise KeyError(f"Trade ID {trade_id} not found")

    with open(TRADES_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return updated


def list_trades(status: Optional[str] = None) -> list[dict]:
    """Return all trades, optionally filtered by status."""
    _ensure_file()
    with open(TRADES_FILE, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if status:
        rows = [r for r in rows if r["status"] == status]
    return rows


def get_trade(trade_id: str) -> dict:
    trades = list_trades()
    for t in trades:
        if t["id"] == trade_id:
            return t
    raise KeyError(f"Trade ID {trade_id} not found")
