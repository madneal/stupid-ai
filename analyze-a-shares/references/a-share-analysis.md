# A-Share Analysis Reference

## Ticker Normalization

Common mainland China A-share suffixes:

- `.SH`: Shanghai Stock Exchange, including main board `600`, `601`, `603`, `605` and STAR Market `688`.
- `.SZ`: Shenzhen Stock Exchange, including main board `000`, `001`, `002`, `003` and ChiNext `300`.
- `.BJ`: Beijing Stock Exchange, commonly `4`, `8`, and `920` prefixes.

If a user gives only a company name, resolve the exact listed security and exchange. Some groups have A-share, H-share, ADR, or multiple listed entities.

## Data Sources

Use primary or close-to-primary sources when possible:

- Exchange data and notices: SSE, SZSE, BSE.
- Filings and announcements: CNINFO, exchange disclosure pages, company investor relations.
- Financial statements: annual, interim, and quarterly reports.
- Market data and aggregators: Wind, Choice, iFinD, Eastmoney, Sina Finance, Tencent Finance, AkShare, Tushare. Identify aggregator data as aggregator data.

For current data, record the source timestamp or trading date. For historical data, note adjustment type: unadjusted, forward-adjusted, or backward-adjusted.

## Metrics Checklist

Market and technical:

- Last price, daily change, volume, turnover, amount traded, market cap.
- Relative performance versus CSI 300, CSI 500, STAR 50, ChiNext, or a sector index.
- Returns over 1, 5, 20, 60, 120, and 250 trading days where data exists.
- Moving averages: 20, 60, 120, 250 sessions.
- RSI 14, drawdown, volatility, volume expansion or contraction.

Fundamental:

- Revenue growth, net profit growth, gross margin, net margin.
- Operating cash flow, free cash flow, leverage, interest coverage.
- ROE, ROA, asset turnover, working capital quality.
- Segment mix, customer concentration, geographic exposure.
- Dividend yield, payout ratio, buybacks when applicable.

Valuation:

- PE TTM, forward PE if estimates are sourced, PB, PS, dividend yield.
- Compare against the company's own history and direct peers.
- Explain when PE is not meaningful because earnings are negative, cyclical, or distorted by one-offs.

Catalysts and risks:

- Earnings releases, guidance, order backlog, product launches.
- Policy changes, regulatory actions, procurement rules, subsidies.
- Major shareholder pledge, reduction plans, restricted-share unlocks.
- Related-party transactions, litigation, penalties, audit opinions.
- Liquidity, ST or delisting risk, suspension risk.

## Output Pattern

Use this order unless the user asks for a different shape:

1. Bottom line: stance and one-sentence rationale.
2. Data snapshot: ticker, exchange, as-of date, price, market cap, valuation, key recent returns.
3. What is working: strongest factual positives.
4. What is not working: strongest factual negatives and uncertainty.
5. Valuation and peers: whether the multiple looks cheap, fair, or expensive relative to growth and sector.
6. Technical picture: trend, momentum, levels, and volume.
7. Watchpoints: events or data that would change the view.
8. Research disclaimer.

## Reasoning Rules

- Do not treat a cheap PE as enough for a bullish conclusion.
- Do not treat a rising price as confirmation without checking volume, sector context, and news.
- Be explicit about A-share market constraints such as daily limit moves, trading suspensions, ST risk, and mainland disclosure timing.
- Separate realized results from analyst forecasts.
- Keep recommendations conditional unless the user provides investor profile, horizon, and risk tolerance.
