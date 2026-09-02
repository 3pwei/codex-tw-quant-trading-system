from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from .engine import BacktestResult


def save_report(result: BacktestResult, output_dir: str | Path) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    trades_path = output / "trades.csv"
    equity_path = output / "equity.csv"
    summary_path = output / "summary.json"
    plot_path = output / "equity.png"

    result.trades.to_csv(trades_path, index=False)
    result.equity.to_csv(equity_path, index=False)
    summary_path.write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.plot(result.equity["timestamp"], result.equity["equity"], linewidth=1.8)
    ax.set_title("Intraday Backtest Equity")
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity (NTD)")
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)

    return {
        "trades": trades_path,
        "equity": equity_path,
        "summary": summary_path,
        "plot": plot_path,
    }

