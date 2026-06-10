from __future__ import annotations


REQUIRED_COMPARISON_FIELDS = (
    "strategy_id",
    "run_id",
    "starting_equity",
    "current_equity",
    "strategy_return",
    "spy_return",
    "excess_return",
    "max_drawdown",
    "trade_count",
    "rejected_trade_count",
)


def format_strategy_comparison(reports: list[dict]) -> str:
    headers = (
        "strategy_id",
        "run_id",
        "starting equity",
        "current equity",
        "strategy return",
        "SPY return",
        "excess return",
        "max drawdown",
        "trade count",
        "rejected trade count",
    )
    rows = [
        (
            report["strategy_id"],
            report["run_id"],
            _money(report["starting_equity"]),
            _money(report["current_equity"]),
            _percent(report["strategy_return"]),
            _percent(report["spy_return"]),
            _percent(report["excess_return"]),
            _percent(report["max_drawdown"]),
            str(report["trade_count"]),
            str(report["rejected_trade_count"]),
        )
        for report in reports
    ]

    widths = [
        max(len(str(value)) for value in column)
        for column in zip(headers, *rows)
    ]
    lines = [
        "Strategy Comparison",
        _format_row(headers, widths),
        _format_row(tuple("-" * width for width in widths), widths),
    ]
    lines.extend(_format_row(row, widths) for row in rows)
    return "\n".join(lines)


def _format_row(values: tuple[str, ...], widths: list[int]) -> str:
    return " | ".join(value.ljust(width) for value, width in zip(values, widths))


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2%}"

