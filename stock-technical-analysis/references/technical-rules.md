# Technical Screening Rules

## Indicators

- **MA5/MA10/MA20/MA60**: short-term trend, medium support, and trend slope.
- **RSI14**: momentum. Above 78 is usually short-term overheated; below 35 is weak or oversold.
- **KDJ-J**: fast sentiment. Above 100 is overheated; below 0 is weak/oversold.
- **MACD histogram**: short-term momentum. Positive and rising is preferred.
- **Distance from MA20**: entry risk. Above 18%-25% means the trend is strong but the entry is crowded.

## Classification

`A可买`:
- close > MA5 > MA10 > MA20
- MACD histogram > 0 and improving
- RSI < 78, KDJ-J < 100, distance from MA20 < 18%

`A-试仓`:
- close > MA5 and close > MA10
- MACD histogram > 0
- not severely overheated

`B强趋势等回踩`:
- clear uptrend
- but at least one overheat condition: RSI >= 78, KDJ-J >= 100, or distance from MA20 >= 18%

`B观察`:
- price is above MA20 or MACD is positive
- but price has not reclaimed MA5, or momentum is fading

`C不买`:
- price below MA20, MACD negative, moving-average structure broken, or recent returns show broad weakness

## Buy Zone Logic

- For `A可买` and `A-试仓`: prefer a pullback near MA5-MA10, not a chase above the intraday high.
- For `B强趋势等回踩`: wait for MA10-MA20 pullback; do not chase the close.
- For `B观察`: wait for price to reclaim MA5, then buy on a controlled MA10 pullback.
- For `C不买`: require at least a reclaim of MA20 before reconsidering.

## Portfolio Guardrails

- Do not recommend adding to a user's already oversized single-stock position unless the user explicitly wants to average down and the cost reduction is meaningful.
- Favor ETFs over single stocks when the user has limited cash, high single-stock concentration, or discomfort with high-priced shares.
- Call out when an entry is technically strong but unsuitable because of overconcentration, high volatility, ST status, or fundamental deterioration.
