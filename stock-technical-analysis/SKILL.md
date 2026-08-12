---
name: stock-technical-analysis
description: Batch screen China A-share stocks, A-share ETFs, and mainland-listed sector funds by technical indicators. Use when the user provides a watchlist, pasted JSON/CSV/text symbols, screenshots transcribed into tickers, or asks which stocks/ETFs have short-term upward trends, which are worth buying, what buy zones/stop levels to use, or how to rank A-share candidates by MA, RSI, KDJ, MACD, returns, support, and resistance.
---

# Stock Technical Analysis

## Overview

Use this skill to turn a China A-share/ETF watchlist into a technical screening table with short-term trend classification, buy zones, invalidation levels, and a concise action ranking.

This skill is for repeatable technical research. It should not present outputs as guaranteed predictions or personalized financial advice.

## Workflow

1. Normalize the watchlist.
   - Accept exchange-prefixed forms such as `SH688008`, `SZ159516`, `688008.SH`, `159516.SZ`, or JSON objects with `symbol` and `name`.
   - Exclude non-mainland securities unless the user explicitly asks to include them.
   - Preserve user-provided names when available.

2. Get current data.
   - For latest market-sensitive work, use current market data and state the exact trading date/time.
   - Use `scripts/technical_screen.py` for repeatable Tencent K-line calculations.
   - If network data is unavailable, ask for OHLCV exports or analyze only the supplied data.

3. Compute the technical set.
   - Moving averages: MA5, MA10, MA20, MA60.
   - Momentum: RSI14, KDJ K/D/J, MACD histogram.
   - Trend strength: 5-day return, 20-day return, price distance from MA20.
   - Risk zones: 20-day high/low, stop/invalidation level.

4. Classify candidates.
   - `A可买`: price > MA5 > MA10 > MA20, MACD histogram positive/improving, and not severely overbought.
   - `A-试仓`: price is above MA5/MA10 with positive MACD, but trend structure is less complete.
   - `B强趋势等回踩`: trend is strong but RSI/KDJ/distance from MA20 are overheated.
   - `B观察`: above MA20 but not yet above MA5, or trend needs confirmation.
   - `C不买`: weak trend, below key moving averages, negative MACD, or broken structure.

5. Provide an action-focused answer.
   - Start with the top 3-5 names and whether to buy now, wait for pullback, or avoid.
   - Include concrete buy zone, add zone if useful, invalidation/stop level, and why.
   - Separate ETFs from single stocks when both are present.
   - Flag overextended names clearly: strong trend does not equal good entry.

## Script

Run the bundled script from the skill directory or pass an absolute path:

```bash
python3 scripts/technical_screen.py --format markdown
python3 scripts/technical_screen.py watchlist.json --format markdown
python3 scripts/technical_screen.py SH688008 SZ159516 SH588200 --format markdown
python3 scripts/technical_screen.py watchlist.json --format json
```

Input formats:

- No input: use the bundled default watchlist in [references/default-watchlist.json](references/default-watchlist.json).
- JSON array of objects with `symbol` and optional `name`.
- Plain text containing A-share symbols.
- Direct command-line symbols.

The script fetches qfq daily K-line data from Tencent's public endpoint, calculates indicators, ranks candidates, and prints a table.

## Reference

Read [references/technical-rules.md](references/technical-rules.md) when the user asks for the reasoning behind classification thresholds, buy-zone logic, or indicator interpretation.
