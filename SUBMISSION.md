# DoraHacks submission draft (copy-paste into the form)

## Project name
TradingPalantir

## One-liner
A Palantir-style AI command center for autonomous spot trading on BNB Chain: scores all 128 eligible tokens 1-100, arms only high-conviction candidates, enters on a proprietary FVG/VWAP/EMA strategy, rides confirmed trends with adaptive Trendflex exits, and executes through Trust Wallet Agent Kit only after deterministic risk approval.

## Description
Most trading bots ask an LLM whether to buy. TradingPalantir doesn't.

**The decision cycle:**
1. **Score everything.** Every rescreen cycle the agent scores all 128 eligible BEP-20 tokens (1-100) using CoinMarketCap data: volume/liquidity, momentum, social buzz (CMC trending narratives + news), technical breakout-readiness and EMA trend structure from on-chain candles. The score breakdown for every coin is journaled — full transparency.
2. **Arm only the best.** Top-20 watchlist; only coins above an adaptive threshold (85-92 depending on the market regime derived from CMC global + derivatives metrics) become "armed". A token-safety firewall (honeypot/liquidity checks via TWAK) caps anything suspicious.
3. **Hunt the setup.** Armed coins are monitored bar-by-bar across 4 timeframes for a proprietary deterministic entry: FVG + session VWAP + multi-timeframe EMA alignment + Williams-fractal stop.
4. **Confirm, don't decide.** On a signal, a two-pass Claude gateway (analyst proposes → independent risk reviewer challenges, fed with CMC news/social/derivatives evidence) can only confirm, veto, or cut size — capability-gated, it can never increase risk or execute.
5. **Deterministic final authority.** Risk Governor enforces the eligible-token allowlist, position limits, notional caps, and a tiered drawdown guard (8% → defensive, 12% → block, 18% → emergency flatten). A DailyTradeMonitor guarantees the ≥1 trade/day competition rule without ever bypassing risk gates.
6. **Don't cut the winner.** The signature edge: positions start with a fixed 3R take-profit, but once the custom OscMatrix indicator (Money Flow + Ehlers Trendflex) confirms the trend, the agent latches "ride mode" — the TP is removed and the position is held while Trendflex > 0, protected by R-multiple stop progression (+1R→breakeven, +2R→+1R) and an ATR trail. Exit on Trendflex flip.

**Execution** is 100% Trust Wallet Agent Kit (self-custody): swaps, stop-loss automations, portfolio, on-chain competition registration, and an ERC-8004 on-chain agent identity.

Runs 24/7 on a VPS under systemd, with a Streamlit command center, SQLite+JSONL decision journal, 35 unit tests, and a historical replay harness.

## Links
- Repo: https://github.com/Tyled763/tradingpalantir
- Agent wallet (BSC): 0xAaD844634247B124Eb8cA93378fF7E3608E7a290
- Registration tx: https://bscscan.com/tx/0xd75091adb91e58ac97523311057b96254b752ef6ef9abddfb4649b52d403780e
- ERC-8004 identity: agentId 132867 — https://bscscan.com/tx/0xb434847f03f449df059e13ad09447dc3b3ca6765dbc3ca551a9217bc90e180a7
- Live execution proof: https://bscscan.com/tx/0x2c92229dbfaba5da418f6dbd0803352b38b5ea9e9c2607e89fb38e9127625709

## Tech stack
Python (asyncio) · Trust Wallet Agent Kit (@trustwallet/cli) · CoinMarketCap Pro API + CMC MCP (AI Agent Hub) · GeckoTerminal OHLCV · Anthropic Claude (two-pass analyst/reviewer) · Streamlit · SQLite · BSC mainnet / PancakeSwap
