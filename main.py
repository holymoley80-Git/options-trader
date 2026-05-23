#!/usr/bin/env python3
"""
Options screening and paper trade logger.
Usage:
  python main.py chain AAPL --exp 2026-06-18 --type call
  python main.py screen AAPL --exp 2026-06-18 --spread-type short_put_vertical --width 5,10
  python main.py log open --strategy "short put vertical" --underlying AAPL \\
      --legs "sell AAPL 190P / buy AAPL 185P @ 2026-06-20" --credit 1.45
  python main.py log close <trade_id> --debit 0.50
  python main.py log list
  python main.py greeks --spot 190 --strike 195 --tte 30 --iv 0.28 --type call
"""

import argparse
import json
import sys
from datetime import date, datetime

from options_trader.display import (
    console, print_options_chain, print_spread_results, print_trades,
    print_candidates_table, print_positions_table,
)
from options_trader.greeks import calculate_greeks
from options_trader.trade_log import close_trade, list_trades, open_trade


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_chain(args):
    from options_trader.polygon_client import get_options_chain, get_underlying_price
    spot = get_underlying_price(args.ticker)
    contracts = get_options_chain(
        underlying=args.ticker,
        expiration_date=args.exp,
        contract_type=args.type,
        strike_price_gte=spot * 0.85 if not args.all else None,
        strike_price_lte=spot * 1.15 if not args.all else None,
        limit=args.limit,
    )
    print_options_chain(contracts, args.ticker, spot)
    console.print(f"[dim]{len(contracts)} contracts retrieved[/]")


def cmd_greeks(args):
    T = args.tte / 365
    g = calculate_greeks(
        S=args.spot,
        K=args.strike,
        T=T,
        r=args.rate,
        sigma=args.iv,
        option_type=args.type,
    )
    console.print(f"\n[bold cyan]Greeks[/] — {args.type.upper()}  S={args.spot}  K={args.strike}  "
                  f"TTE={args.tte}d  IV={args.iv*100:.1f}%\n")
    for field, val in g.__dict__.items():
        console.print(f"  {field:<12} {val}")
    console.print()


def _screen_one(ticker: str, spread_type: str, widths: list[float],
                exp: str | None, dte: int, min_pop: float) -> tuple[str, float, list]:
    """Fetch, build, and screen spreads for a single ticker. Returns (ticker, spot, spreads)."""
    from options_trader.polygon_client import get_options_chain, get_underlying_price
    from options_trader.yahoo_client import get_expirations, get_nearest_expiration
    from options_trader.spread_screener import build_spread_pairs, screen_spreads

    spot = get_underlying_price(ticker)
    target_exp = exp or get_nearest_expiration(ticker, dte)
    if not target_exp:
        return ticker, spot, []

    contracts = get_options_chain(
        underlying=ticker,
        expiration_date=target_exp,
        strike_price_gte=spot * 0.80,
        strike_price_lte=spot * 1.20,
    )
    pairs = build_spread_pairs(contracts, spread_type, widths)
    return ticker, spot, screen_spreads(pairs, spread_type, min_pop=min_pop)


def cmd_screen(args):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    widths   = [float(w) for w in args.width.split(",")]
    tickers  = args.watchlist if args.watchlist else [args.ticker]
    min_pop  = args.min_pop
    is_multi = len(tickers) > 1

    console.print(
        f"\n[bold]Spread screen — {len(tickers)} ticker(s)  "
        f"type={args.spread_type}  width(s)={args.width}  "
        f"dte≈{args.dte}  min_pop={min_pop:.0%}[/]\n"
    )

    all_spreads: list = []
    spots: dict[str, float] = {}
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_screen_one, t, args.spread_type, widths, args.exp, args.dte, min_pop): t
            for t in tickers
        }
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                t, spot, spreads = fut.result()
                spots[t] = spot
                all_spreads.extend(spreads)
                status = f"[green]{len(spreads)} passed[/]" if spreads else "[dim]0 passed[/]"
                console.print(f"  {t:<6} ${spot:>8.2f}  {status}")
            except Exception as e:
                errors.append(ticker)
                console.print(f"  [red]{ticker:<6} ERROR: {e}[/]")

    if errors:
        console.print(f"\n[yellow]Failed tickers: {', '.join(errors)}[/]")

    if not all_spreads:
        console.print("\n[dim]No spreads passed.[/]")
        return

    # Rank by credit/width descending
    all_spreads.sort(key=lambda s: -s.credit_to_width)

    console.print()
    if is_multi:
        print_spread_results(all_spreads, None, None, None, spots=spots)
    else:
        t0, s0 = tickers[0], spots.get(tickers[0])
        exp0 = all_spreads[0].expiration if all_spreads else args.exp
        print_spread_results(all_spreads, t0, s0, exp0)


# ---------------------------------------------------------------------------
# New subcommand handlers
# ---------------------------------------------------------------------------

def cmd_candidates(args):
    from options_trader.db import init_db
    init_db()

    if args.candidates_action == "list":
        from options_trader.db import get_candidates
        candidates = get_candidates(status="pending")
        print_candidates_table(candidates)
        console.print(f"[dim]{len(candidates)} pending candidate(s)[/]")

    elif args.candidates_action == "accept":
        from options_trader.inventory import accept_candidate
        pos_id = accept_candidate(args.id)
        console.print(f"[green]Candidate {args.id} accepted → Position {pos_id} opened.[/]")

    elif args.candidates_action == "reject":
        from options_trader.inventory import reject_candidate
        reason = args.reason or "manual reject"
        reject_candidate(args.id, reason)
        console.print(f"[yellow]Candidate {args.id} rejected: {reason}[/]")

    elif args.candidates_action == "fill":
        from options_trader.inventory import fill_candidate_slots
        added = fill_candidate_slots()
        console.print(f"[green]Added {added} candidate(s)[/]")


def cmd_positions(args):
    from options_trader.db import init_db
    init_db()

    if args.positions_action == "list":
        from options_trader.db import get_positions
        status = args.status or None
        positions = get_positions(status=status)
        print_positions_table(positions, show_snapshots=True)
        console.print(f"[dim]{len(positions)} position(s)[/]")

    elif args.positions_action == "close":
        from options_trader.inventory import close_position
        reason = args.reason or "manual close"
        close_position(args.id, args.debit, reason)
        console.print(f"[green]Position {args.id} closed (debit={args.debit:.2f}, reason={reason})[/]")


def cmd_report(args):
    from options_trader.db import init_db
    init_db()

    from options_trader.report import generate_daily_report
    html = generate_daily_report([])
    from datetime import date
    from pathlib import Path
    today_str = date.today().isoformat()
    report_dir = Path(__file__).parent / "reports" / "daily"
    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"{today_str}.html"
    out_path.write_text(html, encoding="utf-8")
    console.print(f"[green]Report saved to {out_path}[/]")

    if args.email:
        try:
            from scheduler import send_email_report
            send_email_report(html, today_str)
            console.print("[green]Report emailed.[/]")
        except Exception as e:
            console.print(f"[red]Email failed: {e}[/]")


def cmd_scheduler(args):
    import subprocess, sys
    from pathlib import Path
    scheduler = Path(__file__).parent / "scheduler.py"
    cmd = [sys.executable, str(scheduler)]
    if args.dry_run:
        cmd.append("--dry-run")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_log(args):
    if args.log_action == "open":
        trade_id = open_trade(
            strategy=args.strategy,
            underlying=args.underlying,
            legs=args.legs,
            entry_debit_credit=args.debit if args.debit else -(args.credit or 0),
            notes=args.notes or "",
        )
        console.print(f"[green]Trade opened:[/] {trade_id}")

    elif args.log_action == "close":
        row = close_trade(
            trade_id=args.trade_id,
            exit_debit_credit=args.debit if args.debit else -(args.credit or 0),
            notes=args.notes or "",
        )
        pnl = float(row["pnl"])
        color = "green" if pnl >= 0 else "red"
        console.print(f"[{color}]Trade {args.trade_id} closed. P&L: {pnl:+.2f}[/]")

    elif args.log_action == "list":
        trades = list_trades(status=args.status)
        print_trades(trades)


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description="Options screener and paper trade logger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # chain
    p_chain = sub.add_parser("chain", help="Fetch and display options chain")
    p_chain.add_argument("ticker")
    p_chain.add_argument("--exp", help="Expiration date YYYY-MM-DD")
    p_chain.add_argument("--type", choices=["call", "put"], help="Filter by contract type")
    p_chain.add_argument("--limit", type=int, default=100)
    p_chain.add_argument("--all", action="store_true", help="Don't restrict strike range around spot")

    # greeks
    p_greeks = sub.add_parser("greeks", help="Calculate Black-Scholes Greeks locally")
    p_greeks.add_argument("--spot", type=float, required=True)
    p_greeks.add_argument("--strike", type=float, required=True)
    p_greeks.add_argument("--tte", type=float, required=True, help="Days to expiration")
    p_greeks.add_argument("--iv", type=float, required=True, help="Implied vol as decimal (e.g. 0.28)")
    p_greeks.add_argument("--type", choices=["call", "put"], default="call")
    p_greeks.add_argument("--rate", type=float, default=0.05, help="Risk-free rate (default 0.05)")

    # screen
    p_screen = sub.add_parser("screen", help="Screen spread pairs against Passarelli strategy rules")
    ticker_group = p_screen.add_mutually_exclusive_group(required=True)
    ticker_group.add_argument("ticker", nargs="?", help="Single ticker to screen")
    ticker_group.add_argument(
        "--watchlist", action="store_true",
        help="Screen the built-in 20-ticker watchlist in parallel",
    )
    p_screen.add_argument("--exp", help="Expiration date YYYY-MM-DD (overrides --dte)")
    p_screen.add_argument(
        "--dte", type=int, default=30,
        help="Target days-to-expiration when --exp is not given (default: 30)",
    )
    p_screen.add_argument(
        "--spread-type",
        dest="spread_type",
        choices=["short_call_vertical", "short_put_vertical",
                 "long_call_vertical",  "long_put_vertical"],
        default="short_put_vertical",
        help="Spread strategy to screen (default: short_put_vertical)",
    )
    p_screen.add_argument(
        "--width", default="5",
        help="Comma-separated spread width(s) in dollars, e.g. 5 or 5,10 (default: 5)",
    )
    p_screen.add_argument(
        "--min-pop", dest="min_pop", type=float, default=0.65,
        help="Minimum probability of profit 0-1 (default: 0.65)",
    )

    # candidates
    p_cand = sub.add_parser("candidates", help="Manage screening candidates")
    cand_sub = p_cand.add_subparsers(dest="candidates_action", required=True)

    cand_sub.add_parser("list", help="List pending candidates")

    p_cand_accept = cand_sub.add_parser("accept", help="Accept a candidate (creates position)")
    p_cand_accept.add_argument("id", type=int, help="Candidate ID")

    p_cand_reject = cand_sub.add_parser("reject", help="Reject a candidate")
    p_cand_reject.add_argument("id", type=int, help="Candidate ID")
    p_cand_reject.add_argument("--reason", default="manual reject", help="Rejection reason")

    cand_sub.add_parser("fill", help="Trigger fill_candidate_slots() immediately")

    # positions
    p_pos = sub.add_parser("positions", help="Manage open/closed positions")
    pos_sub = p_pos.add_subparsers(dest="positions_action", required=True)

    p_pos_list = pos_sub.add_parser("list", help="List positions")
    p_pos_list.add_argument("--status", choices=["open", "closed", "expired"],
                            help="Filter by status")

    p_pos_close = pos_sub.add_parser("close", help="Close a position")
    p_pos_close.add_argument("id", type=int, help="Position ID")
    p_pos_close.add_argument("--debit", type=float, required=True, help="Exit debit per share")
    p_pos_close.add_argument("--reason", default="manual close", help="Reason for closing")

    # report
    p_report = sub.add_parser("report", help="Report generation")
    report_sub = p_report.add_subparsers(dest="report_action", required=True)
    p_rep_gen = report_sub.add_parser("generate", help="Generate daily HTML report")
    p_rep_gen.add_argument("--email", action="store_true", help="Send report via email after generating")

    # scheduler
    p_sched = sub.add_parser("scheduler", help="Run the daily scheduler")
    sched_sub = p_sched.add_subparsers(dest="scheduler_action", required=True)
    p_sched_run = sched_sub.add_parser("run", help="Run scheduler now")
    p_sched_run.add_argument("--dry-run", dest="dry_run", action="store_true",
                              help="Skip email")

    # log
    p_log = sub.add_parser("log", help="Paper trade log operations")
    log_sub = p_log.add_subparsers(dest="log_action", required=True)

    p_open = log_sub.add_parser("open", help="Open a new paper trade")
    p_open.add_argument("--strategy", required=True)
    p_open.add_argument("--underlying", required=True)
    p_open.add_argument("--legs", required=True, help="Description of legs")
    group = p_open.add_mutually_exclusive_group(required=True)
    group.add_argument("--debit", type=float)
    group.add_argument("--credit", type=float)
    p_open.add_argument("--notes", default="")

    p_close = log_sub.add_parser("close", help="Close an open paper trade")
    p_close.add_argument("trade_id")
    group2 = p_close.add_mutually_exclusive_group(required=True)
    group2.add_argument("--debit", type=float)
    group2.add_argument("--credit", type=float)
    p_close.add_argument("--notes", default="")

    p_list = log_sub.add_parser("list", help="List paper trades")
    p_list.add_argument("--status", choices=["open", "closed", "expired"])

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "screen":
        from options_trader.watchlist import WATCHLIST
        if args.watchlist:
            args.watchlist = WATCHLIST
            args.ticker = None
        else:
            args.watchlist = None

    dispatch = {
        "chain": cmd_chain,
        "greeks": cmd_greeks,
        "screen": cmd_screen,
        "log": cmd_log,
        "candidates": cmd_candidates,
        "positions": cmd_positions,
        "report": cmd_report,
        "scheduler": cmd_scheduler,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
