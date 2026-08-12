#!/usr/bin/env python3
"""Full-market A-share MA5 uptrend screener.

Fetches current A-share universe from Eastmoney and daily qfq K-lines from
Tencent, then ranks stocks whose close is above a rising 5-day moving average.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import subprocess
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


EASTMONEY_LIST = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&"
    "fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&"
    "fields=f12,f14,f2,f3,f5,f6,f17,f18"
)
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


@dataclass
class Stock:
    symbol: str
    name: str
    price: float
    pct: float
    volume: float
    amount: float


def fetch_json(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            time.sleep(0.4 + attempt * 0.6)
            try:
                result = subprocess.run(
                    ["curl", "-sL", "--retry", "2", "--max-time", str(timeout), url],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip():
                    return json.loads(result.stdout)
            except Exception:
                pass
    if last_error:
        raise last_error
    raise RuntimeError("request failed")


def market_prefix(code: str) -> str:
    return "sh" if code.startswith(("6", "5", "9")) else "sz"


def fetch_universe() -> list[Stock]:
    all_rows = []
    for page in range(1, 80):
        payload = fetch_json(EASTMONEY_LIST.format(page=page))
        rows = payload.get("data", {}).get("diff", []) or []
        if not rows:
            break
        all_rows.extend(rows)
        time.sleep(0.08)
        if len(rows) < 100:
            break
    universe: list[Stock] = []
    seen: set[str] = set()
    for row in all_rows:
        code = str(row.get("f12", ""))
        name = str(row.get("f14", ""))
        if len(code) != 6 or not code[0].isdigit():
            continue
        if code in seen:
            continue
        seen.add(code)
        if "ST" in name.upper() or "退" in name:
            continue
        try:
            price = float(row.get("f2"))
            pct = float(row.get("f3"))
            volume = float(row.get("f5") or 0)
            amount = float(row.get("f6") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        universe.append(Stock(f"{market_prefix(code)}{code}", name, price, pct, volume, amount))
    return universe


def fetch_kline(symbol: str, days: int = 80) -> list[dict[str, float | str]]:
    param = f"{symbol},day,,,{days},qfq"
    url = f"{TENCENT_KLINE}?param={urllib.parse.quote(param)}"
    payload = fetch_json(url)
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
                    "volume": float(row[5]) if len(row) > 5 else 0.0,
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return parsed


def avg(values: list[float], window: int, end: int | None = None) -> float | None:
    items = values[:end] if end is not None else values
    if len(items) < window:
        return None
    return sum(items[-window:]) / window


def analyze(stock: Stock) -> dict[str, Any] | None:
    try:
        rows = fetch_kline(stock.symbol)
    except Exception:
        return None
    if len(rows) < 25:
        return None
    closes = [float(r["close"]) for r in rows]
    volumes = [float(r["volume"]) for r in rows]
    ma5 = avg(closes, 5)
    ma5_prev = avg(closes, 5, -1)
    ma10 = avg(closes, 10)
    ma20 = avg(closes, 20)
    vol5_prev = avg(volumes, 5, -1)
    if not all(v is not None and v > 0 for v in [ma5, ma5_prev, ma10, ma20]):
        return None
    close = closes[-1]
    ret5 = close / closes[-6] - 1 if len(closes) >= 6 and closes[-6] else 0.0
    ret20 = close / closes[-21] - 1 if len(closes) >= 21 and closes[-21] else 0.0
    dist_ma5 = close / ma5 - 1
    dist_ma20 = close / ma20 - 1
    vol_ratio = volumes[-1] / vol5_prev if vol5_prev else math.nan
    structure = close > ma5 > ma10 > ma20
    ma5_up = ma5 > ma5_prev
    above_ma5 = close > ma5
    if not (above_ma5 and ma5_up):
        return None

    score = 0
    score += 35 if structure else 15
    score += 20 if close > ma10 else 0
    score += 15 if close > ma20 else 0
    score += 15 if 0.02 <= ret5 <= 0.12 else 5 if ret5 > 0.12 else 0
    score += 10 if 0 <= dist_ma5 <= 0.04 else 3 if dist_ma5 <= 0.08 else -10
    score += 5 if 0.8 <= vol_ratio <= 2.5 else 0
    if stock.amount < 200_000_000:
        score -= 10
    if ret5 > 0.18 or dist_ma5 > 0.08 or stock.pct > 8:
        tag = "强趋势等回踩"
    elif structure and score >= 80:
        tag = "MA5上升可关注"
    elif close > ma10 and score >= 60:
        tag = "试仓候选"
    else:
        tag = "观察"

    return {
        "code": stock.symbol.upper(),
        "name": stock.name,
        "date": rows[-1]["date"],
        "close": close,
        "pct": stock.pct,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ret5": ret5,
        "ret20": ret20,
        "dist_ma5": dist_ma5,
        "dist_ma20": dist_ma20,
        "vol_ratio": vol_ratio,
        "amount": stock.amount,
        "score": score,
        "tag": tag,
    }


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    universe = fetch_universe()
    started = time.time()
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as executor:
        for item in executor.map(analyze, universe):
            if item:
                results.append(item)
    results.sort(key=lambda x: (x["tag"] != "MA5上升可关注", -x["score"], x["dist_ma5"]))
    print(f"as_of={results[0]['date'] if results else 'N/A'} universe={len(universe)} matched={len(results)} elapsed={time.time() - started:.1f}s")
    print("|排名|代码|名称|收盘|涨跌%|MA5|MA10|MA20|5日涨幅|距MA5|量比5日|标签|")
    print("|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for idx, row in enumerate(results[:limit], 1):
        print(
            f"|{idx}|{row['code']}|{row['name']}|{row['close']:.2f}|{row['pct']:.2f}|"
            f"{row['ma5']:.2f}|{row['ma10']:.2f}|{row['ma20']:.2f}|"
            f"{row['ret5'] * 100:.2f}%|{row['dist_ma5'] * 100:.2f}%|"
            f"{row['vol_ratio']:.2f}|{row['tag']}|"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
