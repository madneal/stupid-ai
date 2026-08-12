#!/usr/bin/env python3
"""Screen China A-share stocks and ETFs with technical indicators."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def normalize_symbol(raw: str) -> str | None:
    value = raw.strip().upper()
    if not value:
        return None
    value = value.replace(".", "")
    if value.startswith(("SH", "SZ")) and len(value) >= 8:
        exchange = value[:2].lower()
        code = re.sub(r"\D", "", value[2:])[:6]
    elif value.endswith(("SH", "SZ")) and len(value) >= 8:
        exchange = value[-2:].lower()
        code = re.sub(r"\D", "", value[:-2])[-6:]
    else:
        digits = re.sub(r"\D", "", value)
        if len(digits) < 6:
            return None
        code = digits[-6:]
        exchange = "sh" if code.startswith(("5", "6", "9")) else "sz"
    if len(code) != 6:
        return None
    return f"{exchange}{code}"


def display_symbol(symbol: str) -> str:
    return f"{symbol[:2].upper()}{symbol[2:]}"


def extract_watchlist(inputs: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in inputs:
        path = Path(item)
        if path.exists():
            text = path.read_text(encoding="utf-8")
            try:
                data = json.loads(text)
                if isinstance(data, list):
                    for entry in data:
                        if isinstance(entry, dict):
                            raw = str(entry.get("symbol", ""))
                            symbol = normalize_symbol(raw)
                            if symbol:
                                items.append({"symbol": symbol, "name": str(entry.get("name", ""))})
                    continue
            except json.JSONDecodeError:
                pass
            symbols = re.findall(r"\b(?:SH|SZ)?\d{6}(?:\.(?:SH|SZ))?\b", text, re.I)
        else:
            symbols = [item]
        for raw in symbols:
            symbol = normalize_symbol(raw)
            if symbol:
                items.append({"symbol": symbol, "name": ""})

    deduped: dict[str, dict[str, str]] = {}
    for item in items:
        if item["symbol"] not in deduped or item.get("name"):
            deduped[item["symbol"]] = item
    return list(deduped.values())


def fetch_kline(symbol: str, days: int) -> list[dict[str, Any]]:
    param = f"{symbol},day,,,{days},qfq"
    url = f"{TENCENT_KLINE}?param={urllib.parse.quote(param)}"
    with urllib.request.urlopen(url, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    data = payload.get("data", {})
    key = next(iter(data), None)
    if not key:
        return []
    rows = data[key].get("qfqday") or data[key].get("day") or []
    parsed = []
    for row in rows:
        try:
            parsed.append(
                {
                    "date": row[0],
                    "open": float(row[1]),
                    "close": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "volume": float(row[5]) if len(row) > 5 else None,
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return parsed


def avg(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def ema(values: list[float], window: int) -> list[float]:
    if not values:
        return []
    factor = 2 / (window + 1)
    out: list[float] = []
    previous = values[0]
    for value in values:
        previous = value if not out else value * factor + previous * (1 - factor)
        out.append(previous)
    return out


def rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) <= window:
        return None
    gains = 0.0
    losses = 0.0
    for index in range(len(closes) - window, len(closes)):
        change = closes[index] - closes[index - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / losses)


def kdj(rows: list[dict[str, Any]], window: int = 9) -> dict[str, float]:
    k_value = 50.0
    d_value = 50.0
    j_value = 50.0
    for index, row in enumerate(rows):
        sample = rows[max(0, index - window + 1) : index + 1]
        low = min(item["low"] for item in sample)
        high = max(item["high"] for item in sample)
        rsv = 50.0 if high == low else (row["close"] - low) / (high - low) * 100
        k_value = 2 / 3 * k_value + 1 / 3 * rsv
        d_value = 2 / 3 * d_value + 1 / 3 * k_value
        j_value = 3 * k_value - 2 * d_value
    return {"K": k_value, "D": d_value, "J": j_value}


def macd(closes: list[float]) -> dict[str, float | None]:
    if len(closes) < 26:
        return {"dif": None, "dea": None, "hist": None, "hist_prev": None}
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    hist = [(a - b) * 2 for a, b in zip(dif, dea)]
    return {"dif": dif[-1], "dea": dea[-1], "hist": hist[-1], "hist_prev": hist[-2] if len(hist) > 1 else None}


def pct(value: float | None) -> float | None:
    if value is None or math.isnan(value) or math.isinf(value):
        return None
    return value * 100


def window_return(closes: list[float], periods: int) -> float | None:
    if len(closes) <= periods or closes[-periods - 1] == 0:
        return None
    return pct(closes[-1] / closes[-periods - 1] - 1)


def classify(metrics: dict[str, Any]) -> str:
    close = metrics["close"]
    ma5 = metrics["ma5"]
    ma10 = metrics["ma10"]
    ma20 = metrics["ma20"]
    hist = metrics["macd_hist"]
    hist_prev = metrics["macd_hist_prev"]
    rsi14 = metrics["rsi14"]
    j_value = metrics["kdj_j"]
    dist20 = metrics["dist20"]
    if None in (ma5, ma10, ma20, hist, rsi14, j_value, dist20):
        return "C不买"
    uptrend = close > ma5 > ma10 > ma20
    overheated = rsi14 >= 78 or j_value >= 100 or dist20 >= 18
    macd_improving = hist > 0 and (hist_prev is None or hist >= hist_prev)
    recovering = close > ma5 and close > ma10 and hist > 0
    if uptrend and not overheated and macd_improving:
        return "A可买"
    if uptrend and overheated:
        return "B强趋势等回踩"
    if recovering and not overheated:
        return "A-试仓"
    if close > ma20 and hist > 0:
        return "B观察"
    return "C不买"


def buy_zone(metrics: dict[str, Any]) -> str:
    cls = metrics["class"]
    ma5 = metrics["ma5"]
    ma10 = metrics["ma10"]
    ma20 = metrics["ma20"]
    if cls.startswith("A") and ma5 and ma10:
        low = min(ma5 * 0.995, ma10 * 1.010)
        high = max(ma5 * 0.995, ma10 * 1.010)
        return f"{low:.3f}-{high:.3f}"
    if cls.startswith("B强") and ma10 and ma20:
        low = min(ma10 * 0.990, ma20 * 1.030)
        high = max(ma10 * 0.990, ma20 * 1.030)
        return f"{low:.3f}-{high:.3f}"
    if cls == "B观察" and ma5 and ma10:
        return f"站回{ma5:.3f}后，回踩{ma10:.3f}附近"
    if ma20:
        return f"站回{ma20:.3f}再看"
    return "数据不足"


def score(metrics: dict[str, Any]) -> int:
    cls = metrics["class"]
    score_value = {"A可买": 70, "A-试仓": 60, "B强趋势等回踩": 45, "B观察": 35, "C不买": 0}.get(cls, 0)
    if metrics.get("close") and metrics.get("ma20") and metrics["close"] > metrics["ma20"]:
        score_value += 8
    if metrics.get("macd_hist") and metrics["macd_hist"] > 0:
        score_value += 6
    if metrics.get("ret5") and metrics["ret5"] > 0:
        score_value += 4
    if metrics.get("rsi14") and metrics["rsi14"] > 85:
        score_value -= 10
    if metrics.get("dist20") and metrics["dist20"] > 25:
        score_value -= 10
    return score_value


def analyze(item: dict[str, str], days: int) -> dict[str, Any]:
    rows = fetch_kline(item["symbol"], days)
    if len(rows) < 30:
        return {"symbol": display_symbol(item["symbol"]), "name": item.get("name", ""), "error": "insufficient kline data"}
    closes = [row["close"] for row in rows]
    latest = rows[-1]
    previous = rows[-2]
    kdj_values = kdj(rows)
    macd_values = macd(closes)
    ma20 = avg(closes, 20)
    metrics: dict[str, Any] = {
        "symbol": display_symbol(item["symbol"]),
        "name": item.get("name", ""),
        "date": latest["date"],
        "close": latest["close"],
        "daily_change": pct(latest["close"] / previous["close"] - 1) if previous["close"] else None,
        "ma5": avg(closes, 5),
        "ma10": avg(closes, 10),
        "ma20": ma20,
        "ma60": avg(closes, 60),
        "rsi14": rsi(closes),
        "kdj_j": kdj_values["J"],
        "macd_hist": macd_values["hist"],
        "macd_hist_prev": macd_values["hist_prev"],
        "ret5": window_return(closes, 5),
        "ret20": window_return(closes, 20),
        "dist20": pct(latest["close"] / ma20 - 1) if ma20 else None,
        "high20": max(row["high"] for row in rows[-20:]),
        "low20": min(row["low"] for row in rows[-20:]),
    }
    metrics["class"] = classify(metrics)
    metrics["buy_zone"] = buy_zone(metrics)
    metrics["stop"] = metrics["ma20"] * 0.97 if metrics.get("ma20") else None
    metrics["score"] = score(metrics)
    return metrics


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def render_markdown(results: list[dict[str, Any]]) -> str:
    usable = [item for item in results if not item.get("error")]
    errors = [item for item in results if item.get("error")]
    lines = [
        "| 排名 | 代码 | 名称 | 收盘 | 涨跌% | MA5 | MA10 | MA20 | RSI | J | 5日% | 20日% | 距20日% | 分级 | 买入区间 | 失效位 |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
    ]
    for index, item in enumerate(sorted(usable, key=lambda row: row["score"], reverse=True), 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    item["symbol"],
                    item.get("name") or "-",
                    fmt(item["close"], 3),
                    fmt(item["daily_change"], 2),
                    fmt(item["ma5"], 3),
                    fmt(item["ma10"], 3),
                    fmt(item["ma20"], 3),
                    fmt(item["rsi14"], 1),
                    fmt(item["kdj_j"], 1),
                    fmt(item["ret5"], 2),
                    fmt(item["ret20"], 2),
                    fmt(item["dist20"], 2),
                    item["class"],
                    item["buy_zone"],
                    fmt(item["stop"], 3),
                ]
            )
            + " |"
        )
    if errors:
        lines.extend(["", "Data warnings:"])
        for item in errors:
            lines.append(f"- {item['symbol']} {item.get('name', '')}: {item['error']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Screen A-share stocks and ETFs with technical indicators.")
    parser.add_argument("inputs", nargs="*", help="Watchlist JSON/text file or symbols such as SH688008 SZ159516. If omitted, the bundled default watchlist is used.")
    parser.add_argument("--days", type=int, default=120, help="Daily K-line rows to fetch. Default: 120.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--default-watchlist", action="store_true", help="Use the bundled default watchlist even when no input file is supplied.")
    args = parser.parse_args()

    inputs = args.inputs
    if args.default_watchlist or not inputs:
        default_path = Path(__file__).resolve().parents[1] / "references" / "default-watchlist.json"
        inputs = [str(default_path)]
    watchlist = extract_watchlist(inputs)
    if not watchlist:
        print("No A-share symbols found.", file=sys.stderr)
        return 2
    results = [analyze(item, args.days) for item in watchlist]
    results.sort(key=lambda item: item.get("score", -999), reverse=True)
    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
