"""SQLite persistence layer for options trader."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# Always resolve DB path relative to project root (parent of this file's package)
_PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = _PROJECT_ROOT / "data" / "options_trader.db"


@contextmanager
def get_conn():
    """Yield a sqlite3 connection with WAL mode, foreign keys, and Row factory.
    Commits on success, rolls back on exception."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables and indexes if they don't already exist."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS candidates (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker        TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                legs_json     TEXT NOT NULL,
                credit        REAL NOT NULL,
                max_risk      REAL NOT NULL,
                pop           REAL NOT NULL,
                greeks_json   TEXT NOT NULL,
                iv            REAL,
                generated_at  TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                reject_reason TEXT,
                grade         TEXT
            );

            CREATE TABLE IF NOT EXISTS positions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id         INTEGER REFERENCES candidates(id),
                ticker               TEXT NOT NULL,
                strategy             TEXT NOT NULL,
                legs_json            TEXT NOT NULL,
                entry_credit         REAL NOT NULL,
                entry_greeks_json    TEXT NOT NULL,
                entry_date           TEXT NOT NULL,
                entry_price_underlying REAL,
                status               TEXT NOT NULL DEFAULT 'open',
                exit_debit           REAL,
                exit_date            TEXT,
                exit_greeks_json     TEXT,
                pnl                  REAL,
                exit_reason          TEXT,
                grade                TEXT,
                paper                INTEGER NOT NULL DEFAULT 0,
                entry_iv             REAL
            );

            CREATE TABLE IF NOT EXISTS daily_snapshots (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id           INTEGER NOT NULL REFERENCES positions(id),
                date                  TEXT NOT NULL,
                current_price_underlying REAL,
                current_greeks_json   TEXT,
                unrealized_pnl        REAL,
                exit_signal_triggered INTEGER NOT NULL DEFAULT 0,
                exit_signal_reason    TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_candidates_status
                ON candidates(status);
            CREATE INDEX IF NOT EXISTS idx_candidates_ticker
                ON candidates(ticker);
            CREATE INDEX IF NOT EXISTS idx_positions_status
                ON positions(status);
            CREATE INDEX IF NOT EXISTS idx_snapshots_position_date
                ON daily_snapshots(position_id, date);
        """)
        _migrate_db(conn)


def _migrate_db(conn):
    """Add columns introduced after initial schema (idempotent)."""
    migrations = [
        "ALTER TABLE candidates ADD COLUMN grade TEXT",
        "ALTER TABLE positions ADD COLUMN grade TEXT",
        "ALTER TABLE positions ADD COLUMN paper INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE positions ADD COLUMN entry_iv REAL",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def insert_candidate(
    ticker: str,
    strategy: str,
    legs_json_str: str,
    credit: float,
    max_risk: float,
    pop: float,
    greeks_json_str: str,
    iv: float | None,
) -> int:
    """Insert a new candidate and return its id."""
    generated_at = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO candidates
               (ticker, strategy, legs_json, credit, max_risk, pop, greeks_json, iv, generated_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (ticker, strategy, legs_json_str, credit, max_risk, pop, greeks_json_str, iv, generated_at),
        )
        return cur.lastrowid


def update_candidate_status(id: int, status: str, reject_reason: str | None = None):
    """Update a candidate's status (pending/accepted/rejected)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE candidates SET status=?, reject_reason=? WHERE id=?",
            (status, reject_reason, id),
        )


def update_candidate_grade(id: int, grade: str | None):
    """Set or clear the A/B/C grade on a candidate."""
    with get_conn() as conn:
        conn.execute("UPDATE candidates SET grade=? WHERE id=?", (grade, id))


def get_candidates(status: str | None = None) -> list[dict]:
    """Return candidates as a list of dicts, optionally filtered by status."""
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM candidates WHERE status=? ORDER BY generated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM candidates ORDER BY generated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def insert_position(
    candidate_id: int | None,
    ticker: str,
    strategy: str,
    legs_json_str: str,
    entry_credit: float,
    entry_greeks_json_str: str,
    entry_price_underlying: float | None,
    paper: int = 0,
    entry_iv: float | None = None,
    grade: str | None = None,
) -> int:
    """Insert a new open position and return its id."""
    entry_date = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (candidate_id, ticker, strategy, legs_json, entry_credit,
                entry_greeks_json, entry_date, entry_price_underlying, status,
                paper, entry_iv, grade)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (candidate_id, ticker, strategy, legs_json_str, entry_credit,
             entry_greeks_json_str, entry_date, entry_price_underlying,
             paper, entry_iv, grade),
        )
        return cur.lastrowid


def update_position_close(
    id: int,
    exit_debit: float,
    exit_greeks_json_str: str,
    pnl: float,
    exit_reason: str,
):
    """Mark a position as closed."""
    exit_date = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE positions
               SET status='closed', exit_debit=?, exit_date=?,
                   exit_greeks_json=?, pnl=?, exit_reason=?
               WHERE id=?""",
            (exit_debit, exit_date, exit_greeks_json_str, pnl, exit_reason, id),
        )


def get_positions(status: str | None = None, paper: int | None = None) -> list[dict]:
    """Return positions as a list of dicts, optionally filtered by status and/or paper flag."""
    conditions = []
    params = []
    if status is not None:
        conditions.append("status=?")
        params.append(status)
    if paper is not None:
        conditions.append("paper=?")
        params.append(paper)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM positions {where} ORDER BY entry_date DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------

def insert_snapshot(
    position_id: int,
    date_str: str,
    spot: float | None,
    greeks_json_str: str | None,
    unrealized_pnl: float | None,
    signal_triggered: bool,
    signal_reason: str | None,
):
    """Insert a daily snapshot for a position."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_snapshots
               (position_id, date, current_price_underlying, current_greeks_json,
                unrealized_pnl, exit_signal_triggered, exit_signal_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (position_id, date_str, spot, greeks_json_str,
             unrealized_pnl, 1 if signal_triggered else 0, signal_reason),
        )


def get_latest_snapshot(position_id: int) -> dict | None:
    """Return the most recent snapshot for a position, or None."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM daily_snapshots
               WHERE position_id=?
               ORDER BY date DESC, id DESC
               LIMIT 1""",
            (position_id,),
        ).fetchone()
        return dict(row) if row else None


def get_snapshots(position_id: int) -> list[dict]:
    """Return all snapshots for a position, oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_snapshots WHERE position_id=? ORDER BY date ASC, id ASC",
            (position_id,),
        ).fetchall()
        return [dict(r) for r in rows]
