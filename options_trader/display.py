"""Rich-based terminal display helpers."""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console(width=200)


def print_options_chain(contracts: list[dict], underlying: str, spot: float):
    table = Table(
        title=f"{underlying} Options Chain  |  Spot: ${spot:.2f}",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
    )
    cols = [
        ("Ticker", "cyan"),
        ("Type", "white"),
        ("Strike", "white"),
        ("Exp", "white"),
        ("Bid", "green"),
        ("Ask", "red"),
        ("IV", "yellow"),
        ("Delta", "magenta"),
        ("Gamma", "magenta"),
        ("Theta", "magenta"),
        ("Vega", "magenta"),
        ("OI", "white"),
        ("Vol", "white"),
    ]
    for name, style in cols:
        table.add_column(name, style=style, justify="right" if name not in ("Ticker", "Type", "Exp") else "left")

    for c in contracts:
        iv_str = f"{c['iv']*100:.1f}%" if c.get("iv") else "—"
        table.add_row(
            c.get("ticker") or "—",
            (c.get("contract_type") or "—").upper(),
            f"{c['strike']:.0f}" if c.get("strike") else "—",
            str(c.get("expiration") or "—"),
            f"{c['bid']:.2f}" if c.get("bid") else "—",
            f"{c['ask']:.2f}" if c.get("ask") else "—",
            iv_str,
            f"{c['delta']:.3f}" if c.get("delta") is not None else "—",
            f"{c['gamma']:.4f}" if c.get("gamma") is not None else "—",
            f"{c['theta']:.4f}" if c.get("theta") is not None else "—",
            f"{c['vega']:.4f}" if c.get("vega") is not None else "—",
            str(c.get("open_interest") or "—"),
            str(c.get("volume") or "—"),
        )
    console.print(table)


def print_screen_result(result, contract: dict):
    color = "green" if result.passed else "red"
    status = "PASS" if result.passed else "FAIL"
    lines = [f"[bold {color}]{status}[/] — {result.strategy}"]
    if result.reasons:
        lines.append("\n[red]Fail reasons:[/]")
        for r in result.reasons:
            lines.append(f"  • {r}")
    if result.warnings:
        lines.append("\n[yellow]Warnings:[/]")
        for w in result.warnings:
            lines.append(f"  • {w}")
    console.print(Panel("\n".join(lines), title=contract.get("ticker", ""), border_style=color))


def print_spread_results(
    spreads: list,
    underlying: str | None,
    spot: float | None,
    exp: str | None,
    spots: dict[str, float] | None = None,
):
    """
    Render spread results as a table.
    Single-ticker: pass underlying/spot/exp, leave spots=None.
    Watchlist:     pass spots={ticker: price}, leave underlying/spot/exp as None.
    """
    if not spreads:
        console.print("[dim]No spreads passed the screen.[/]")
        return

    multi = underlying is None
    spread_type = spreads[0].spread_type.replace("_", " ").title()
    title = (
        f"Watchlist — {spread_type} Screen  |  {len(spreads)} spread(s) passed"
        if multi
        else f"{underlying} — {spread_type} Screen  |  Spot: ${spot:.2f}  Exp: {exp}"
    )

    table = Table(title=title, box=box.SIMPLE_HEAVY, show_lines=True)

    if multi:
        table.add_column("Ticker",   style="cyan",  justify="left")
        table.add_column("Exp",      justify="left")
        table.add_column("Spot",     justify="right")
    table.add_column("Short K",  justify="right", style="red")
    table.add_column("Long K",   justify="right", style="cyan")
    table.add_column("Width",    justify="right")
    table.add_column("Credit",   justify="right", style="green")
    table.add_column("Cr/Wid",   justify="right", style="green")
    table.add_column("Max Risk", justify="right", style="red")
    table.add_column("B/E",      justify="right")
    table.add_column("PoP",      justify="right", style="yellow")
    table.add_column("Δ short",  justify="right", style="magenta")
    table.add_column("Net Δ",    justify="right", style="magenta")
    table.add_column("Net Θ",    justify="right", style="magenta")
    table.add_column("Net V",    justify="right", style="magenta")
    table.add_column("Short OI", justify="right")
    table.add_column("Long OI",  justify="right")
    table.add_column("Warn",     style="yellow", no_wrap=True, max_width=36)

    for sp in spreads:
        warn_str = "; ".join(sp.warnings) if sp.warnings else ""
        ticker_spot = (spots or {}).get(sp.underlying)
        core = [
            f"{sp.short_strike:.0f}",
            f"{sp.long_strike:.0f}",
            f"{sp.width:.0f}",
            f"{sp.net_credit:.2f}",
            f"{sp.credit_to_width:.0%}",
            f"{sp.max_risk:.2f}",
            f"{sp.break_even:.2f}",
            f"{sp.pop_approx:.0%}",
            f"{sp.short_delta_abs:.2f}",
            f"{sp.net_delta:+.3f}",
            f"{sp.net_theta:+.3f}",
            f"{sp.net_vega:+.3f}",
            str(sp.short_oi or "—"),
            str(sp.long_oi or "—"),
            warn_str[:36] if warn_str else "",
        ]
        row = ([sp.underlying, sp.expiration,
                f"${ticker_spot:.2f}" if ticker_spot else "—"] + core) if multi else core
        table.add_row(*row)

    console.print(table)
    if not multi:
        console.print(f"[dim]{len(spreads)} spread(s) passed[/]\n")


def print_candidates_table(candidates: list[dict]):
    """Render pending candidates as a Rich table."""
    import json
    if not candidates:
        console.print("[dim]No pending candidates.[/]")
        return

    table = Table(title="Pending Candidates", box=box.SIMPLE_HEAVY, show_lines=True)
    for col, style, justify in [
        ("ID",       "cyan",    "right"),
        ("Ticker",   "cyan",    "left"),
        ("Strategy", "white",   "left"),
        ("Exp",      "white",   "left"),
        ("Short K",  "red",     "right"),
        ("Long K",   "green",   "right"),
        ("Width",    "white",   "right"),
        ("Credit",   "green",   "right"),
        ("Cr/W",     "green",   "right"),
        ("PoP",      "yellow",  "right"),
        ("Short Δ",  "magenta", "right"),
        ("Net Δ",    "magenta", "right"),
        ("Net Θ",    "magenta", "right"),
        ("Net V",    "magenta", "right"),
        ("IV",       "yellow",  "right"),
        ("Status",   "white",   "left"),
        ("Generated","dim",     "left"),
    ]:
        table.add_column(col, style=style, justify=justify)

    for c in candidates:
        try:
            legs = json.loads(c.get("legs_json") or "{}")
        except Exception:
            legs = {}
        try:
            greeks = json.loads(c.get("greeks_json") or "{}")
        except Exception:
            greeks = {}

        width = legs.get("width") or 1
        credit = c.get("credit") or 0
        cr_w = credit / width if width else 0
        pop = c.get("pop") or 0
        iv = c.get("iv") or 0
        is_condor = legs.get("type") == "iron_condor"

        if is_condor:
            short_k = (f"{legs.get('short_put_strike', '?'):.0f}P / "
                       f"{legs.get('short_call_strike', '?'):.0f}C")
            long_k  = (f"{legs.get('long_put_strike', '?'):.0f}P / "
                       f"{legs.get('long_call_strike', '?'):.0f}C")
            short_d = (greeks.get("short_put_delta", 0) + greeks.get("short_call_delta", 0)) / 2
        else:
            short_k = f"{legs.get('short_strike', '—')}"
            long_k  = f"{legs.get('long_strike', '—')}"
            short_d = greeks.get("short_delta", 0)

        status = c.get("status", "pending")
        status_color = {"pending": "cyan", "accepted": "green", "rejected": "red"}.get(status, "white")

        table.add_row(
            str(c.get("id", "?")),
            c.get("ticker", "—"),
            c.get("strategy", "—"),
            legs.get("expiration", "—"),
            short_k,
            long_k,
            f"{width:.0f}" if isinstance(width, (int, float)) else str(width),
            f"${credit:.2f}",
            f"{cr_w:.0%}",
            f"{pop:.0%}",
            f"{short_d:.3f}",
            f"{greeks.get('net_delta', 0):+.3f}",
            f"{greeks.get('net_theta', 0):+.3f}",
            f"{greeks.get('net_vega', 0):+.3f}",
            f"{iv:.0%}",
            f"[{status_color}]{status}[/]",
            (c.get("generated_at") or "—")[:16],
        )
    console.print(table)


def print_positions_table(positions: list[dict], show_snapshots: bool = True):
    """Render positions as a Rich table."""
    import json
    if not positions:
        console.print("[dim]No positions found.[/]")
        return

    table = Table(title="Positions", box=box.SIMPLE_HEAVY, show_lines=True)
    cols = [
        ("ID",          "cyan",    "right"),
        ("Ticker",      "cyan",    "left"),
        ("Strategy",    "white",   "left"),
        ("Exp",         "white",   "left"),
        ("Entry Date",  "white",   "left"),
        ("Entry Cr",    "green",   "right"),
        ("Status",      "white",   "left"),
        ("P&L (1lot)",  "green",   "right"),
        ("Exit Reason", "yellow",  "left"),
    ]
    if show_snapshots:
        cols.insert(6, ("Unrl P&L", "green", "right"))
        cols.insert(7, ("Cost-Close", "red", "right"))

    for col, style, justify in cols:
        table.add_column(col, style=style, justify=justify)

    from options_trader.db import get_latest_snapshot

    for pos in positions:
        try:
            legs = json.loads(pos.get("legs_json") or "{}")
        except Exception:
            legs = {}

        status = pos.get("status", "?")
        status_color = {"open": "cyan", "closed": "white", "expired": "yellow"}.get(status, "white")
        pnl = pos.get("pnl")
        pnl_color = "green" if pnl and pnl >= 0 else "red"
        pnl_str = f"[{pnl_color}]${pnl * 100:+.2f}[/]" if pnl is not None else "—"

        row = [
            str(pos.get("id", "?")),
            pos.get("ticker", "—"),
            pos.get("strategy", "—"),
            legs.get("expiration", "—"),
            pos.get("entry_date", "—"),
            f"${pos.get('entry_credit', 0):.2f}",
            f"[{status_color}]{status}[/]",
            pnl_str,
            (pos.get("exit_reason") or "—")[:30],
        ]

        if show_snapshots:
            snap = None
            if status == "open":
                try:
                    snap = get_latest_snapshot(pos["id"])
                except Exception:
                    pass
            unrl = snap.get("unrealized_pnl") if snap else None
            unrl_color = "green" if unrl and unrl >= 0 else "red"
            unrl_str = f"[{unrl_color}]${unrl * 100:+.2f}[/]" if unrl is not None else "—"

            entry_credit = pos.get("entry_credit", 0)
            cost_close = (entry_credit - unrl) if unrl is not None else None
            cost_str = f"${cost_close:.2f}" if cost_close is not None else "—"

            row.insert(6, unrl_str)
            row.insert(7, cost_str)

        table.add_row(*row)

    console.print(table)


def print_trades(trades: list[dict]):
    if not trades:
        console.print("[dim]No trades found.[/]")
        return
    table = Table(title="Paper Trades", box=box.SIMPLE_HEAVY)
    for col in ["ID", "Opened", "Closed", "Status", "Strategy", "Underlying", "Entry", "Exit", "P&L", "Notes"]:
        table.add_column(col)
    for t in trades:
        pnl = t.get("pnl", "")
        pnl_color = "green" if pnl and float(pnl) >= 0 else "red"
        pnl_str = f"[{pnl_color}]{pnl}[/]" if pnl else "—"
        status_color = {"open": "cyan", "closed": "white", "expired": "yellow"}.get(t["status"], "white")
        table.add_row(
            t["id"],
            t["opened_at"][:10],
            t.get("closed_at", "")[:10] or "—",
            f"[{status_color}]{t['status']}[/]",
            t["strategy"],
            t["underlying"],
            t["entry_debit_credit"],
            t.get("exit_debit_credit") or "—",
            pnl_str,
            (t.get("notes") or "")[:40],
        )
    console.print(table)
