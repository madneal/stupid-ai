#!/usr/bin/env python3
"""Compute repeatable A-share technical metrics from an OHLCV CSV export."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


ALIASES = {
    "date": ("date", "trade_date", "datetime", "time", "日期", "交易日期"),
    "open": ("open", "open_price", "开盘", "开盘价"),
    "high": ("high", "high_price", "最高", "最高价"),
    "low": ("low", "low_price", "最低", "最低价"),
    "close": ("close", "close_price", "收盘", "收盘价", "最新价"),
    "volume": ("volume", "vol", "成交量"),
    "amount": ("amount", "turnover", "成交额"),
    "turnover_rate": ("turnover_rate", "turnoverrate", "换手率"),
}


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = (
        str(value)
        .strip()
        .replace(",", "")
        .replace("%", "")
        .replace("--", "")
        .replace("N/A", "")
    )
    if not cleaned:
        return None
    unit = 1.0
    if cleaned.endswith("万"):
        unit = 10_000.0
        cleaned = cleaned[:-1]
    elif cleaned.endswith("亿"):
        unit = 100_000_000.0
        cleaned = cleaned[:-1]
    try:
        return float(cleaned) * unit
    except ValueError:
        return None


def parse_date(value: str) -> str:
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return raw


def canonical_header(header: str) -> str:
    normalized = header.strip().lower().replace(" ", "_").replace("-", "_")
    for key, names in ALIASES.items():
        if normalized in {name.lower() for name in names} or header.strip() in names:
            return key
    return normalized


def load_prices(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            row = {canonical_header(k): v for k, v in raw.items() if k is not None}
            if not row.get("date") or parse_number(row.get("close")) is None:
                continue
            rows.append(
                {
                    "date": parse_date(row["date"]),
                    "open": parse_number(row.get("open")),
                    "high": parse_number(row.get("high")),
                    "low": parse_number(row.get("low")),
                    "close": parse_number(row.get("close")),
                    "volume": parse_number(row.get("volume")),
                    "amount": parse_number(row.get("amount")),
                    "turnover_rate": parse_number(row.get("turnover_rate")),
                }
            )
    rows.sort(key=lambda item: item["date"])
    return rows


def pct(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return round(value * 100, 2)


def window_return(closes: list[float], periods: int) -> float | None:
    if len(closes) <= periods or closes[-periods - 1] == 0:
        return None
    return pct(closes[-1] / closes[-periods - 1] - 1)


def moving_average(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return round(sum(values[-window:]) / window, 4)


def annualized_volatility(closes: list[float], window: int = 60) -> float | None:
    if len(closes) < 3:
        return None
    sample = closes[-window:] if len(closes) >= window else closes
    returns = [math.log(sample[i] / sample[i - 1]) for i in range(1, len(sample)) if sample[i - 1] > 0]
    if len(returns) < 2:
        return None
    return pct(statistics.stdev(returns) * math.sqrt(252))


def max_drawdown(closes: list[float], window: int = 250) -> float | None:
    sample = closes[-window:] if len(closes) >= window else closes
    if not sample:
        return None
    peak = sample[0]
    worst = 0.0
    for close in sample:
        peak = max(peak, close)
        if peak:
            worst = min(worst, close / peak - 1)
    return pct(worst)


def rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(len(closes) - window, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise SystemExit("No usable price rows found. Expected at least date and close columns.")
    closes = [float(row["close"]) for row in rows]
    latest = rows[-1]
    previous_close = closes[-2] if len(closes) > 1 else None
    daily_change = None if previous_close in (None, 0) else pct(closes[-1] / previous_close - 1)
    mas = {str(window): moving_average(closes, window) for window in (5, 20, 60, 120, 250)}
    returns = {str(window): window_return(closes, window) for window in (1, 5, 20, 60, 120, 250)}
    warnings = []
    if len(rows) < 60:
        warnings.append("Fewer than 60 rows; medium-term trend and volatility are incomplete.")
    if latest.get("volume") is None:
        warnings.append("Volume column missing; volume confirmation cannot be assessed.")
    if latest.get("turnover_rate") is None:
        warnings.append("Turnover-rate column missing; liquidity analysis is limited.")
    return {
        "as_of": latest["date"],
        "rows": len(rows),
        "latest": {
            "open": latest.get("open"),
            "high": latest.get("high"),
            "low": latest.get("low"),
            "close": latest.get("close"),
            "volume": latest.get("volume"),
            "amount": latest.get("amount"),
            "turnover_rate": latest.get("turnover_rate"),
            "daily_change_pct": daily_change,
        },
        "returns_pct": returns,
        "moving_averages": mas,
        "price_vs_ma_pct": {
            key: None if value in (None, 0) else pct(closes[-1] / value - 1)
            for key, value in mas.items()
        },
        "rsi_14": rsi(closes),
        "annualized_volatility_pct": annualized_volatility(closes),
        "max_drawdown_pct": max_drawdown(closes),
        "warnings": warnings,
    }


def to_markdown(summary: dict[str, Any]) -> str:
    latest = summary["latest"]
    lines = [
        f"## A-share metrics as of {summary['as_of']}",
        "",
        f"- Rows: {summary['rows']}",
        f"- Close: {latest['close']}",
        f"- Daily change: {latest['daily_change_pct']}%",
        f"- Volume: {latest['volume']}",
        f"- Amount: {latest['amount']}",
        f"- Turnover rate: {latest['turnover_rate']}%",
        f"- RSI 14: {summary['rsi_14']}",
        f"- Annualized volatility: {summary['annualized_volatility_pct']}%",
        f"- Max drawdown: {summary['max_drawdown_pct']}%",
        "",
        "| Window | Return % | MA | Price vs MA % |",
        "| --- | ---: | ---: | ---: |",
    ]
    for window in ("5", "20", "60", "120", "250"):
        lines.append(
            f"| {window} | {summary['returns_pct'][window]} | "
            f"{summary['moving_averages'][window]} | {summary['price_vs_ma_pct'][window]} |"
        )
    if summary["warnings"]:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in summary["warnings"])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="CSV with at least date and close columns")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()
    summary = summarize(load_prices(args.csv_path))
    if args.format == "markdown":
        print(to_markdown(summary))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
