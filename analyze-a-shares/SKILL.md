---
name: analyze-a-shares
description: Analyze China A-share equities, A-share ETFs, or mainland-listed indices when users ask about A股, A-shares, A-stock, Shanghai/Shenzhen/Beijing exchange tickers, Chinese stock names, sector comparisons, valuation, technical setup, announcements, or risk context. Use current sourced data for market-sensitive work and the bundled script for repeatable metrics from price CSV exports.
---

# Analyze A-Shares

## Overview

Use this skill for China A-share analysis that combines current market data, fundamentals, valuation, technicals, announcements, policy context, and risk framing. The output should be analytical research, not investment advice.

For detailed market conventions, source suggestions, and output checklists, read [references/a-share-analysis.md](references/a-share-analysis.md) when the request requires more than a quick answer.

## Workflow

1. Identify the security.
   - Normalize tickers to exchange form when possible: `600519.SH`, `000001.SZ`, `688981.SH`, `430047.BJ`.
   - For company names or ambiguous codes, resolve the listed A-share target before analyzing.
   - Distinguish A-shares from Hong Kong H-shares, ADRs, B-shares, funds, and indices.

2. Set the as-of date.
   - If the user asks for latest, today, current price, news, filings, ratings, or regulations, use current sources and cite them.
   - State the exact data timestamp or trading date. China A-share sessions normally use China Standard Time.
   - If live data is unavailable, say what is stale and avoid implying intraday precision.

3. Gather evidence from separate categories.
   - Market: recent price, volume, turnover, market cap, index and sector performance.
   - Fundamentals: revenue, profit, margin, cash flow, balance sheet, ROE, dividend, major shareholder changes.
   - Valuation: PE TTM, PB, PS, EV/EBITDA when available, and peer/range comparison.
   - Catalysts and risks: exchange announcements, CNINFO filings, earnings calendar, policy, supply/demand, litigation, pledges, restricted-share unlocks.
   - Technicals: trend, moving averages, relative strength, volume confirmation, support/resistance, drawdown, volatility.

4. Analyze, do not overstate.
   - Separate facts, calculations, and inference.
   - Compare against peers or relevant indices rather than judging a stock in isolation.
   - Highlight data gaps, one-off accounting items, liquidity limits, and policy sensitivity.
   - Avoid price targets unless the user explicitly asks and sufficient assumptions are provided.

5. Produce a structured result.
   - Start with a concise view: bullish, neutral, bearish, or mixed, with the main reasons.
   - Include a source/data table when multiple data points are used.
   - End with scenario watchpoints and what would change the conclusion.
   - Include a short disclaimer that this is research context, not personalized financial advice.

## Using The Script

When the user provides or exports OHLCV data, run:

```bash
python3 scripts/a_share_metrics.py prices.csv --format markdown
```

Expected CSV columns are flexible. The script recognizes common English and Chinese names for date, open, high, low, close, volume, amount, and turnover rate. It computes returns, moving-average position, RSI, drawdown, volatility, and data-quality warnings.

Use the script output as supporting calculations. Still verify market-sensitive numbers against current sources when the user asks for latest conditions.

## Source Discipline

Prefer primary or close-to-primary sources:

- Exchanges: SSE, SZSE, BSE
- Filings and announcements: CNINFO and exchange disclosure pages
- Company investor relations and annual/interim/quarterly reports
- Market data: exchange pages, reputable data vendors, or clearly identified public aggregators

For current or high-stakes answers, cite sources and exact dates. If sources disagree, show the discrepancy instead of silently choosing one.
