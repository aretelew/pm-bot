"""Backtest report: compute metrics and generate HTML reports with charts."""

from __future__ import annotations

import math
from dataclasses import dataclass

import plotly.graph_objects as go
from plotly.subplots import make_subplots
from rich.console import Console
from rich.table import Table

from pm_bot.backtest.engine import BacktestResult

console = Console()


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    brier_score: float | None = None


def compute_metrics(result: BacktestResult) -> BacktestMetrics:
    closing_fills = [f for f in result.fills if f.pnl != 0]
    pnls = [f.pnl for f in closing_fills]

    sharpe = 0.0
    if len(pnls) > 1:
        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        sharpe = (mean_pnl / std) * math.sqrt(252) if std > 0 else 0.0

    return BacktestMetrics(
        total_trades=result.total_trades,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate=result.win_rate,
        total_return_pct=result.total_return_pct,
        total_pnl=result.total_realized_pnl,
        avg_pnl_per_trade=sum(pnls) / len(pnls) if pnls else 0.0,
        max_drawdown=result.max_drawdown,
        sharpe_ratio=sharpe,
    )


def print_report(result: BacktestResult) -> None:
    metrics = compute_metrics(result)

    table = Table(title="Backtest Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total Trades", str(metrics.total_trades))
    table.add_row("Winning Trades", str(metrics.winning_trades))
    table.add_row("Losing Trades", str(metrics.losing_trades))
    table.add_row("Win Rate", f"{metrics.win_rate:.1%}")
    table.add_row("Total PnL", f"${metrics.total_pnl:.2f}")
    table.add_row("Avg PnL / Trade", f"${metrics.avg_pnl_per_trade:.4f}")
    table.add_row("Total Return", f"{metrics.total_return_pct:.2f}%")
    table.add_row("Max Drawdown", f"${metrics.max_drawdown:.2f}")
    table.add_row("Sharpe Ratio", f"{metrics.sharpe_ratio:.2f}")
    table.add_row("Starting Balance", f"${result.starting_balance:.2f}")
    table.add_row("Ending Balance", f"${result.ending_balance:.2f}")

    console.print(table)

    if result.fills:
        fills_table = Table(title="Recent Fills (last 20)")
        fills_table.add_column("Time", style="dim")
        fills_table.add_column("Ticker", style="cyan")
        fills_table.add_column("Action")
        fills_table.add_column("Price", justify="right")
        fills_table.add_column("Qty", justify="right")
        fills_table.add_column("PnL", justify="right")
        fills_table.add_column("Strategy", style="dim")
        for f in result.fills[-20:]:
            pnl_style = "green" if f.pnl > 0 else "red" if f.pnl < 0 else "white"
            fills_table.add_row(
                f.timestamp.strftime("%Y-%m-%d %H:%M"),
                f.ticker,
                f.action,
                f"{f.price}c",
                str(f.quantity),
                f"[{pnl_style}]${f.pnl:.4f}[/{pnl_style}]",
                f.strategy,
            )
        console.print(fills_table)


def generate_html_report(result: BacktestResult, output_path: str = "backtest_report.html") -> str:
    metrics = compute_metrics(result)

    fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=("Equity Curve", "Trade PnL Distribution", "Cumulative PnL", "Trades by Strategy"),
        specs=[[{"type": "scatter"}, {"type": "histogram"}],
               [{"type": "scatter"}, {"type": "bar"}]],
    )

    if result.equity_curve:
        times, equities = zip(*result.equity_curve)
        fig.add_trace(
            go.Scatter(x=list(times), y=list(equities), mode="lines", name="Equity"),
            row=1, col=1,
        )

    pnls = [f.pnl for f in result.fills if f.pnl != 0]
    if pnls:
        fig.add_trace(
            go.Histogram(x=pnls, nbinsx=30, name="PnL Distribution"),
            row=1, col=2,
        )

    if result.fills:
        cum_pnl = []
        running = 0.0
        times_fills = []
        for f in result.fills:
            running += f.pnl
            cum_pnl.append(running)
            times_fills.append(f.timestamp)
        fig.add_trace(
            go.Scatter(x=times_fills, y=cum_pnl, mode="lines", name="Cumulative PnL"),
            row=2, col=1,
        )

    strategy_counts: dict[str, int] = {}
    for f in result.fills:
        strategy_counts[f.strategy] = strategy_counts.get(f.strategy, 0) + 1
    if strategy_counts:
        fig.add_trace(
            go.Bar(x=list(strategy_counts.keys()), y=list(strategy_counts.values()), name="Trades"),
            row=2, col=2,
        )

    fig.update_layout(
        height=800,
        title_text=f"Backtest Report | Return: {metrics.total_return_pct:.2f}% | Sharpe: {metrics.sharpe_ratio:.2f}",
        showlegend=False,
    )

    fig.write_html(output_path)
    console.print(f"[green]Report saved to {output_path}[/green]")
    return output_path
