#!/usr/bin/env python3
"""Daily options trader scheduler.

Usage:
    python scheduler.py
    python scheduler.py --dry-run
"""

import argparse
import logging
import logging.handlers
import sys
from datetime import date, datetime
from pathlib import Path

# Ensure project root is on the path so options_trader imports work when
# called from any working directory.
_PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import os


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging():
    log_dir = _PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "scheduler.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root_logger.addHandler(ch)

    # Rotating file handler (5 MB, 3 backups)
    fh = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    fh.setFormatter(fmt)
    root_logger.addHandler(fh)

    return logging.getLogger("scheduler")


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email_report(html: str, report_date: str) -> None:
    """Send the HTML report via SendGrid. Silently skips if TO_EMAIL not set."""
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    from_email = os.getenv("FROM_EMAIL", "").strip()
    to_email = os.getenv("TO_EMAIL", "").strip()

    if not to_email:
        return
    if not api_key:
        raise RuntimeError("SENDGRID_API_KEY is not set in .env")
    if not from_email:
        raise RuntimeError("FROM_EMAIL is not set in .env")

    import sendgrid
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=f"OptionsTrader Daily Report — {report_date}",
        html_content=html,
    )

    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    response = sg.send(message)

    if response.status_code not in (200, 202):
        raise RuntimeError(
            f"SendGrid returned unexpected status {response.status_code}: {response.body}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Options trader daily scheduler")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run repricing and report generation but skip sending email",
    )
    args = parser.parse_args()

    log = setup_logging()
    log.info("=== Scheduler starting (dry_run=%s) ===", args.dry_run)

    # Step 1: Init database
    try:
        from options_trader.db import init_db
        init_db()
        log.info("Database initialized OK")
    except Exception as e:
        log.error("init_db failed: %s", e, exc_info=True)

    # Step 2: Daily reprice
    reprice_results = []
    try:
        from options_trader.inventory import run_daily_reprice
        reprice_results = run_daily_reprice()
        n_signals = sum(1 for r in reprice_results if r.get("exit_signal_triggered"))
        log.info(
            "run_daily_reprice: %d positions repriced, %d exit signals",
            len(reprice_results), n_signals,
        )
    except Exception as e:
        log.error("run_daily_reprice failed: %s", e, exc_info=True)

    # Step 3: Fill candidate slots
    try:
        from options_trader.inventory import fill_candidate_slots
        added = fill_candidate_slots()
        log.info("fill_candidate_slots: added %d candidates", added)
    except Exception as e:
        log.error("fill_candidate_slots failed: %s", e, exc_info=True)

    # Step 4: Generate report
    html = ""
    today_str = date.today().isoformat()
    try:
        from options_trader.report import generate_daily_report
        html = generate_daily_report(reprice_results)
        log.info("Report generated: %d bytes", len(html))
    except Exception as e:
        log.error("generate_daily_report failed: %s", e, exc_info=True)

    # Step 5: Save report to reports/daily/
    report_path = None
    if html:
        try:
            report_dir = _PROJECT_ROOT / "reports" / "daily"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"{today_str}.html"
            report_path.write_text(html, encoding="utf-8")
            log.info("Report saved to %s", report_path)
        except Exception as e:
            log.error("Saving report failed: %s", e, exc_info=True)

    # Step 6: Send email
    if html and not args.dry_run:
        try:
            send_email_report(html, today_str)
            to = os.getenv("TO_EMAIL", "")
            if to:
                log.info("Report emailed to %s", to)
            else:
                log.info("TO_EMAIL not set — email skipped")
        except Exception as e:
            log.error("send_email_report failed: %s", e, exc_info=True)
    elif args.dry_run:
        log.info("Dry run — email skipped")

    # Summary
    log.info(
        "=== Scheduler complete. Positions repriced: %d | Candidates added: %d | "
        "Report: %s ===",
        len(reprice_results),
        added if "added" in dir() else 0,
        str(report_path) if report_path else "not saved",
    )


if __name__ == "__main__":
    main()
