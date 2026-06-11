# 🔮 TradingPalantir

**A Palantir-style AI command center for autonomous spot trading on BNB Smart Chain.**

Built for **BNB HACK: AI Trading Agent Edition — CoinMarketCap × Trust Wallet**, Track 1 (Autonomous Trading Agents).

> The system does not ask an LLM whether to buy. It scores the whole eligible universe, arms only high-conviction candidates, detects entries with a proprietary deterministic strategy, manages positions through adaptive exits, and executes through Trust Wallet Agent Kit — only after deterministic risk approval. LLMs classify and explain; they never execute.

## On-chain identity & proof

| | |
|---|---|
| Agent wallet (BSC) | `0xAaD844634247B124Eb8cA93378fF7E3608E7a290` |
| Competition registration | [`0xd75091…03780e`](https://bscscan.com/tx/0xd75091adb91e58ac97523311057b96254b752ef6ef9abddfb4649b52d403780e) |
| ERC-8004 identity | **agentId 132867** — [`0xb43484…e180a7`](https://bscscan.com/tx/0xb434847f03f449df059e13ad09447dc3b3ca6765dbc3ca551a9217bc90e180a7) → [agent_card.json](agent_card.json) |
| Live execution proof | buy [`0x2c9222…625709`](https://bscscan.com/tx/0x2c92229dbfaba5da418f6dbd0803352b38b5ea9e9c2607e89fb38e9127625709) · sell [`0x7403d8…963a31`](https://bscscan.com/tx/0x7403d8d783c51ccf34d186a86b84216e31e419cde182edcc664e921abb963a31) |

## The core idea: right coin × right moment × don't cut the winner

```
Stage A  SCORE EVERYTHING      all 128 eligible BEP-20 tokens → composite score 1..100
                               (volume/liquidity 25 · momentum 20 · social 15 ·
                                derivatives pressure ±15 · sector 10 · regime fit 10
                                · safety firewall gate · normalized so 100 = perfect)
Stage B  ARM THE BEST          top-20 watchlist → ARMED set = score ≥ 90 only
Stage C  HUNT THE SETUP        bar-by-bar monitoring of armed coins only →
                               proprietary FVG + VWAP + multi-timeframe-EMA entry
ENTRY    score gate → Claude analyst → independent Claude reviewer → Risk Governor
MANAGE   normal mode: fixed TP (3R) + fractal stop
         confluence (MoneyFlow bull AND Trendflex > 0) latches RIDE MODE:
         TP removed, hold while Trendflex > 0, exit on flip ≤ 0
         protective stops underneath: +1R→breakeven, +2R→+1R, ATR trail (up only)
```

The strongest edge is **adaptive exit optimization**: most bots cut winners with a fixed take-profit. TradingPalantir rides confirmed trends with a custom Trendflex oscillator (Ehlers SuperSmoother + RMS normalization) and protects profit with R-multiple/ATR stop progression — while a hard stop-loss and a deterministic Risk Governor guard every position.

## Architecture

```
CoinMarketCap Pro API + CMC MCP (AI Agent Hub) + GeckoTerminal OHLCV
        ↓
cmc/            clients + skill-equivalents (native replicas of CMC Skills)
intelligence/   market regime · derivatives pressure · opportunity radar (1-100)
                token risk firewall · evidence items (honest partial/blocked)
strategy/       proprietary entry engine (FVG/VWAP/EMA, untouched core)
                + OscMatrix (MoneyFlow + Trendflex) + decision engine
exit/           adaptive exit manager (ride mode, R/ATR protective stops)
risk/           Risk Governor (final authority) · tiered drawdown guard (8/12/18%)
                daily trade monitor (≥1 trade/day, risk-gated fallback)
llm/            Claude two-pass: analyst proposes → independent reviewer challenges
                (capability-gated: can only reduce risk, never execute)
execution/      paper broker + live router → Trust Wallet Agent Kit (self-custody)
journal/        SQLite + JSONL event log (every decision, score, veto, fill)
dashboard/      Streamlit command center
```

### CoinMarketCap usage
- **CMC MCP** (`mcp.coinmarketcap.com/mcp`): trending narratives (social keywords + unique authors), per-coin news, global metrics, derivatives metrics, macro events.
- **CMC Pro API**: batch quotes for the full eligible universe (volume/momentum screening).
- **Skill-equivalents**: native replicas of official CMC Skills (`macro liquidity monitor`, `Detect Market Regime`, `altcoin breakout scanner spot`, `Verify New Token Safety`, `Calculate ATR Trade Risk Levels`, …) implemented over the MCP tools — every output normalized to an `EvidenceItem` with honest `partial`/`blocked` status (no fabricated data).

### Trust Wallet Agent Kit usage
All execution: swaps (PancakeSwap routing via LiquidMesh), stop-loss limit automations (add/list/delete), wallet/portfolio, on-chain competition registration, ERC-8004 identity mint. Self-custody — keys never leave the agent wallet.

### Safety principles
- LLMs **cannot** execute, sign, raise risk, or bypass rules — `size_factor` is clamped ≤ 1 and the deterministic **Risk Governor** is the final authority on every action.
- Hard allowlist: only the 128 eligible BEP-20 tokens (by contract address).
- Every trade has a stop-loss before entry; no averaging down; tiered drawdown guard (defensive → block → emergency flatten).
- Paper mode validated before any live execution; full journal of every decision.

## Reproducing

```bash
git clone https://github.com/Tyled763/tradingpalantir && cd tradingpalantir
pip install -r requirements.txt
npm install -g @trustwallet/cli        # twak
cp .env.example .env                   # fill in your own keys
python3 -m pytest tests/ -q            # 30 unit tests
python3 -m scripts.run_agent           # paper mode by default (DRY_RUN=True)
streamlit run dashboard/app.py         # command center
python3 -m scripts.replay ETH          # historical replay of the full pipeline
```

Live mode: set `DRY_RUN = False` in `config/settings.py` (requires a funded twak wallet; see `.env.example`).

## License

MIT
