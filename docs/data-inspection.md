# Initial CSV inspection — 5 October 2026

Read-only inspection of the 16 local `prices/*_WITHGAP.csv` files. No strategies, indicator searches, profitability calculations, or mandatory engine proof checks were run. No data was repaired. No holdout boundary or sealing mechanism has been configured; this inspection must not be described as an unlock or as unseen-data evaluation.

Following inspection, the user requested HYPE's removal. Its 253-row, 26,805-byte CSV was deleted. The current dataset contains 15 files, 983,121 observed rows, and 106,128,717 bytes. The historical findings and timing below describe the original 16-file inspection; gap totals are unchanged because HYPE had no internal gaps.

## Findings

- 983,374 observed rows; 106,155,522 bytes (106.2 MB decimal).
- Fields: open_time_utc, open, high, low, close, volume_base, volume_quote, trades.
- Earliest observation: 2018-01-01 00:00 UTC. Latest: 2026-10-04 23:00 UTC.
- No duplicate timestamps, backwards timestamps, or null cells were found. This is not full price or source-authenticity validation.
- 324 internal gap episodes counted separately per asset; 259 contain one to four missing hours and 65 exceed four hours. Shared outages across assets are counted more than once.
- 1,252 missing asset-hours within each asset's own first-to-last observation range, about 0.127% of expected rows in those ranges.
- Largest gap: 76 missing hours in BTC, ETH, and BNB, between observed opens at 2018-02-07 23:00 UTC and 2018-02-11 04:00 UTC.
- HYPE had only 253 rows, starting 2026-09-24 11:00 UTC, and was subsequently removed. XMR ends at 2024-02-20 02:00 UTC. The user confirmed that XMR stopped being traded in February 2024: this is an end-of-trading boundary, not a data gap. Retain its earlier history and do not interpolate or extend its trading availability. Terminal-position accounting remains to be defined before runs. Do not require a shared complete history across all assets or fabricate history outside these bounds.

| Pair | Observed rows | Internal gaps | Missing hours | Largest gap (hours) |
| --- | ---: | ---: | ---: | ---: |
| ADAUSDT | 74,132 | 27 | 96 | 10 |
| BCHUSDT | 60,024 | 16 | 38 | 6 |
| BNBUSDT | 76,602 | 29 | 174 | 76 |
| BTCUSDT | 76,602 | 29 | 174 | 76 |
| DOGEUSDT | 63,514 | 19 | 50 | 8 |
| ETHUSDT | 76,602 | 29 | 174 | 76 |
| HYPEUSDT (subsequently removed) | 253 | 0 | 0 | 0 |
| LINKUSDT | 67,579 | 22 | 67 | 10 |
| NEARUSDT | 52,339 | 11 | 24 | 4 |
| SOLUSDT | 53,874 | 11 | 24 | 4 |
| TRXUSDT | 72,805 | 27 | 96 | 10 |
| UNIUSDT | 52,990 | 10 | 23 | 4 |
| XLMUSDT | 73,072 | 26 | 95 | 10 |
| XMRUSDT | 43,210 | 21 | 61 | 10 |
| XRPUSDT | 73,721 | 26 | 95 | 10 |
| ZECUSDT | 66,055 | 21 | 61 | 10 |

## Synchronous feasibility

A local Python/pandas pass that parsed every CSV, converted timestamps, checked ordering/duplicates/nulls, and summarized gaps took 2.227 seconds, excluding Python startup/import time. Files had already been read, so this is not a cold-disk benchmark. Numeric columns plus parsed timestamps occupy approximately 62.9 MB, excluding strings, indexes, derived arrays, interpreter overhead, and concurrent requests.

This is a modest dataset and supports starting with synchronous, one-candidate requests. A worst-case batch visiting all observed rows for each of 25 candidates represents about 24.6 million candidate-row visits, not a runtime prediction. Cache permitted data and reusable calculations. Keep the engine in its own Docker Compose container; separate deployment does not require asynchronous communication.

No complete backtesting runtime has been measured. Stateful execution, portfolio accounting, strategy complexity, feature history, serialization, hardware, and Docker limits can change performance. Before promising latency, benchmark complete representative runs after code is authorized and the synthetic engine proof gate is satisfied. An eight-hour discovery session can consist of many short synchronous calls without an eight-hour HTTP request. Consider asynchronous jobs only if measured requirements later justify them.
