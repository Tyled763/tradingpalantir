# competition.md — Track 1 compliance map

**BNB HACK: AI Trading Agent Edition — CoinMarketCap × Trust Wallet**
Track 1: Autonomous Trading Agents.

| Requirement | How TradingPalantir satisfies it | Proof |
|---|---|---|
| Trade live on BNB Smart Chain | All execution on BSC mainnet via PancakeSwap routing | buy [`0x2c9222…`](https://bscscan.com/tx/0x2c92229dbfaba5da418f6dbd0803352b38b5ea9e9c2607e89fb38e9127625709), sell [`0x7403d8…`](https://bscscan.com/tx/0x7403d8d783c51ccf34d186a86b84216e31e419cde182edcc664e921abb963a31) |
| Use CoinMarketCap data/signals | CMC Pro API (universe screening) + CMC MCP (news, trending/social, global & derivatives metrics, macro) + native skill-equivalents feeding scoring, entry confirmation and macro gate | `cmc/`, `intelligence/` |
| Sign & process transactions through Trust Wallet Agent Kit | twak (`@trustwallet/cli`) is the **sole** execution layer: swaps, SL automations, portfolio, registration, ERC-8004 | `execution/twak_adapter.py` |
| Register agent wallet on-chain before live window | Registered 2026-06-09 | [`0xd75091…`](https://bscscan.com/tx/0xd75091adb91e58ac97523311057b96254b752ef6ef9abddfb4649b52d403780e) |
| Public repo + agent address on DoraHacks | This repository; wallet `0xAaD844634247B124Eb8cA93378fF7E3608E7a290` | — |
| Trade only eligible BEP-20 tokens | Hard allowlist of 128 traded contract addresses (of the 149 eligible; stablecoins excluded) enforced by Risk Governor on every entry | `config/rules.json`, `risk/risk_governor.py`, `tests/test_risk_governor.py` |
| Hold a non-zero balance of in-scope assets at competition start | Agent wallet funded with in-scope assets (~$50 USDT + gas) before the 2026-06-22 open | wallet `0xAaD8…a290` |
| ≥ 1 trade per day during the live window | DailyTradeMonitor: tracks fills per UTC day; proposes a minimal Tier-1 fallback trade in the final window hours — always through all risk gates, never when drawdown guard is active | `risk/daily_trade_monitor.py`, tests |
| Avoid excessive drawdown | Tiered drawdown guard: 12% → defensive (risk & size ×0.5), 18% → block new trades, 25% → emergency flatten; up to 2 concurrent positions, each ~50% of the book ($22.5 notional cap), one-position-per-symbol | `risk/drawdown_guard.py`, `config/rules.json` |
| Reproducible setup / demo | README quick-start, `.env.example`, 49 unit tests, replay script, Streamlit dashboard | `README.md` |
| On-chain proof of activity | Registration + ERC-8004 mint + validated round-trip trade (links above) | — |

## Bonus tracks

- **ERC-8004 identity (BNB stack):** agentId **132867**, minted via twak — [`0xb43484…`](https://bscscan.com/tx/0xb434847f03f449df059e13ad09447dc3b3ca6765dbc3ca551a9217bc90e180a7), agentURI → [agent_card.json](agent_card.json).
- **CMC data:** the agent's coin-selection layer (Stage A scoring) is built on CMC: batch quotes, trending narratives with social keywords/author counts, per-coin news read by the Claude reviewer pair, global + derivatives metrics driving the macro regime gate. Official CMC Skills are replicated natively as `EvidenceItem`-producing pipelines (see `intelligence/`).
- **TWAK:** full lifecycle — wallet, registration, swaps, SL automations (OCO bookkeeping), ERC-8004 — all through twak.

## Operating timeline

- Build window: rebuilt to this architecture 2026-06-10 → 2026-06-11.
- Paper soak: running 24/7 on a VPS under systemd since 2026-06-11.
- Live window 2026-06-22 → 28: same binary, `DRY_RUN=False`.
