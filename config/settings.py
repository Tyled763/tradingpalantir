# =========================
# config.py — Signal Bot v4
# 6 типов сигналов: Trend / Breakout / Reversal × bull/bear
# Таймфреймы: 5m, 15m, 30m, 1H
# =========================
from typing import Dict

# ── Инструменты и таймфреймы ──────────────────────────────
SYMBOLS    = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
TIMEFRAMES = ["5m", "15m", "30m", "1H"]

# ТФ, на которых FVG-сетап порождает торговый вход.
# Все 4 ТФ в TIMEFRAMES по-прежнему используются для мульти-ТФ EMA-confluence;
# здесь ограничивается только ТФ детекции самого входного FVG (5m/15m — шум).
FVG_ENTRY_TIMEFRAMES = ["30m", "1H"]

# ── OKX WS channel names ──────────────────────────────────
TF_TO_CHANNEL: Dict[str, str] = {
    "5m":  "candle5m",
    "15m": "candle15m",
    "30m": "candle30m",
    "1H":  "candle1H",
}

# ── Миллисекунды на таймфрейм ─────────────────────────────
TF_TO_MS: Dict[str, int] = {
    "5m":  5  * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1H":  60 * 60_000,
}

# ── Буфер и прогрев ───────────────────────────────────────
MAX_BARS    = 720
WARMUP_BARS = 720

# ── Индикаторы ────────────────────────────────────────────
EMA_PERIOD     = 89
VWAP_BAND_MULT = 1.0

# ── Фрактальный стоп-лосс (Williams, N баров с каждой стороны) ───
FRACTAL_N = 1

# ── Risk/Reward ───────────────────────────────────────────
RR_RATIO = 3.0

# ── OKX REST (исторический источник для data_manager parquet-кэша) ──
# Оставлено как опциональный бэкап-источник истории; боевой источник — CMC.
OKX_REST_URL   = "https://www.okx.com"
OKX_REST_LIMIT = 100
OKX_REST_PAUSE = 0.15

# ══════════════════════════════════════════════════════════
# BNB HACK — BSC агент (CMC данные + TWAK исполнение)
# Секреты НЕ здесь: TWAK_ACCESS_ID/TWAK_HMAC_SECRET/TWAK_WALLET_PASSWORD/
# CMC_API_KEY берутся из переменных окружения (~/.zshrc).
# ══════════════════════════════════════════════════════════
import os

# ── Сеть BSC ──────────────────────────────────────────────
# "bsc" = mainnet. Для testnet-PoC уточняется chain-key в twak (см. PoC).
NETWORK   = os.environ.get("BNB_NETWORK", "bsc")
USE_TESTNET = NETWORK != "bsc"

# ── Торговая вселенная (BSC-токены на PancakeSwap) ────────
# twak и GeckoTerminal требуют контрактные адреса (символы резолвятся только
# для мейджоров). Каждый токен: {symbol, address}.
TRADE_TOKENS = [
    {"symbol": "WBNB", "address": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"},
    {"symbol": "CAKE", "address": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"},
    {"symbol": "ETH",  "address": "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"},
]
QUOTE_CCY      = "USDT"                                              # вход/выход
QUOTE_ADDRESS  = "0x55d398326f99059fF775485246999027B3197955"        # USDT (BSC, 18dp)

# ── Eligible-вселенная хакатона (149 BEP-20 токенов с CMC) ───
ELIGIBLE_SYMBOLS = [
    "ETH","USDT","USDC","XRP","TRX","DOGE","ZEC","ADA","LINK","BCH","DAI","TON",
    "USD1","USDe","M","LTC","AVAX","SHIB","XAUt","WLFI","H","DOT","UNI","ASTER",
    "DEXE","USDD","ETC","AAVE","ATOM","U","STABLE","FIL","INJ","币安人生","NIGHT",
    "FET","TUSD","BONK","PENGU","CAKE","SIREN","LUNC","ZRO","KITE","FDUSD","BEAT",
    "PIEVERSE","BTT","NFT","EDGE","FLOKI","LDO","B","FF","PENDLE","NEX","STG","AXS",
    "TWT","HOME","RAY","COMP","GWEI","XCN","GENIUS","XPL","BAT","SKYAI","APE","IP",
    "SFP","TAG","NXPC","AB","SAHARA","1INCH","CHEEMS","BANANAS31","RIVER","MYX",
    "RAVE","SNX","FORM","LAB","HTX","USDf","CTM","BDX","SLX","UB","DUCKY","FRAX",
    "BILL","WFI","KOGE","ALE","FRXUSD","USDF","GOMINING","VCNT","GUA","DUSD","SMILEK",
    "0G","BEAM","MY","SOON","REAL","Q","AIOZ","ZIG","YFI","TAC","lisUSD","CYS","ZAMA",
    "TRIA","HUMA","PLUME","ZIL","XPR","ZETA","BabyDoge","NILA","ROSE","VELO","UAI",
    "BRETT","OPEN","BSB","TOSHI","BAS","ACH","AXL","LUR","ELF","KAVA","APR","IRYS",
    "EURI","XUSD","BARD","DUSK","SUSHI","PEAQ","COAI","BDCA","XAUM",
]

# Стейблы/пеги — исключаем из торговли (нет волатильности под стратегию)
STABLE_SYMBOLS = {
    "USDT","USDC","DAI","USD1","USDe","USDD","TUSD","FDUSD","USDf","USDF","FRAX",
    "FRXUSD","DUSD","lisUSD","XUSD","EURI","STABLE","XAUt","XAUM",
}

# ── Скрининг / активный набор ─────────────────────────────
ACTIVE_SET_SIZE       = 30        # сколько монет держим под полным сетапом (Stage B)
SCREEN_INTERVAL_SEC   = 1200      # как часто пересобирать активный набор (Stage A), 20 мин
TOKEN_REGISTRY_FILE   = "config/token_registry.json"

# ── Режим исполнения ──────────────────────────────────────
# True  = свопы только quote-only, ордера не исполняются (валидация без средств).
# False = реальное исполнение на BSC mainnet (после фандинга кошелька).
DRY_RUN = True

# ── Polling (учёт rate-limit GeckoTerminal ~30 req/min) ───
POLL_INTERVAL_SEC = 60        # как часто проверять новый закрытый 5m-бар
BASE_TF           = "5m"      # базовый ТФ генерации сигналов

# ── CMC API ───────────────────────────────────────────────
CMC_API_KEY  = os.environ.get("CMC_API_KEY", "")
CMC_BASE_URL = "https://pro-api.coinmarketcap.com"

# ── Исполнение / комиссии PancakeSwap ─────────────────────
SWAP_SLIPPAGE_PCT = 1.0      # допуск проскальзывания для twak swap, %
ROUNDTRIP_FEE     = 0.0025   # суммарная комиссия вход+выход (PancakeSwap ~0.25%)
EXPECTED_SLIPPAGE = 0.001    # ожидаемое проскальзывание на сторону (для sizing)

# ── Риск ──────────────────────────────────────────────────
RISK_USDT = 2.5              # ТОЛЬКО для scripts/replay.py (офлайн-бэктест); live-sizing берётся из config/rules.json

# ── LLM-надзиратель (Claude) ──────────────────────────────
BRAIN_MODEL = "claude-opus-4-8"   # модель решений (opus = качество; signals редкие)
BRAIN_MODE  = "conservative"      # conservative | balanced | permissive

# ── OscMatrix (кастомный индикатор: Money Flow + Trendflex) ──
OSC_MF_LEN      = 21
OSC_MF_SMOOTH   = 4
OSC_TH_LEN      = 50
OSC_MF_COMPRESS = 0.65
OSC_MID_LEVEL   = 40.0
OSC_TH_MULT     = 1.0
OSC_HW_LEN      = 20
OSC_HW_SMOOTH   = 3
# Вход — БЕЗ фильтра OscMatrix. После входа confluence (mf_bull AND trendflex>0)
# латчит ride-режим: фикс-TP убираем, держим пока trendflex>0, выход при флипе ≤0.
RIDE_MODE_ENABLED = True

# ── Телеметрия / журнал ───────────────────────────────────
PAPER_EQUITY = 50.0        # стартовый бумажный капитал для DRY_RUN equity-кривой
JOURNAL_FILE = "journal.jsonl"

# ── Файлы состояния (переживают рестарт) ──────────────────
SETTINGS_FILE  = "settings.json"
RULES_FILE     = "config/rules.json"
POSITIONS_FILE = "positions.json"
COMPLIANCE_LOG = "compliance.jsonl"

# ══════════════════════════════════════════════════════════
# TradingPalantir — трёхстадийная воронка + risk-tiers (v2)
# ══════════════════════════════════════════════════════════
WATCHLIST_SIZE        = 20    # Stage A: топ-N по score (показываем в дашборде)
SCORE_ENTRY_THRESHOLD = 74    # fallback, если режим неизвестен
# Адаптивный «пол качества» по режиму (v3): порог = floor, реальный armed = top-MONITOR_CAP над ним.
# Хороший рынок → пол ниже → набирается полные 12; risk_off → пол выше → armed сжимается сам.
SCORE_THRESHOLDS = {"risk_on": 70.0, "neutral": 74.0, "risk_off": 80.0}
SCORE_THRESHOLD_OVERHEAT_BUMP = 2.0   # перегрев перпов → +2 к полу
MONITOR_CAP = 12              # макс. монет под мониторингом/armed (rate-limit + фокус)
MONITOR_HYST = 6.0            # гистерезис: держим монету пока score ≥ floor − HYST (анти-churn прогрева)
RESCREEN_INTERVAL_SEC = 1500  # пересчёт скоринга (25 мин)

# Drawdown Guard (ступени, % дневной просадки)
# Ослаблены под концентрированный sizing (2×~50%): даём агрессии дышать,
# жёсткий flatten остаётся катастрофа-тормозом против DQ по drawdown-cap.
DD_DEFENSIVE_PCT = 12.0       # risk x0.5 (+ notional cap x0.5)
DD_BLOCK_PCT     = 18.0       # блок новых входов
DD_FLATTEN_PCT   = 25.0       # emergency flatten

# Daily Trade Monitor (мин. 1 сделка/день в live-окне)
DAILY_MIN_TRADES     = 1
FALLBACK_WINDOW_H    = 4      # за сколько часов до конца дня UTC включать fallback
FALLBACK_TIER1       = ["ETH", "WBNB", "LINK"]   # минимальный размер, со стопом
FALLBACK_NOTIONAL    = 5.0    # USDT на ежедневный compliance round-trip
FALLBACK_TOKEN       = "WBNB"   # гарантированно ликвидный токен для daily round-trip
LIVE_WINDOW          = ("2026-06-22", "2026-06-28")

# Token Risk Firewall
FIREWALL_MIN_LIQUIDITY_USD = 150_000
FIREWALL_CACHE_H           = 24

# Exit Optimizer
ATR_PERIOD      = 14
# ШИРОКИЙ трейл (решение пользователя): срабатывает только на резком развороте,
# иначе позиция держится до флипа trendflex — даём прибыли течь.
ATR_TRAIL_MULT  = 5.0
R_BE_TRIGGER    = 1.0         # +1R -> стоп в безубыток
R_LOCK_TRIGGER  = 2.0         # +2R -> стоп на +1R

# Минимальная дистанция стопа (анти-выбивание шумом): reject слишком тугих сетапов
MIN_STOP_ATR_MULT   = 0.8     # мин. дистанция стопа = 0.8 x ATR(ТФ сигнала)
MIN_STOP_PCT_FALLBACK = 0.005 # 0.5% от входа, если ATR недоступен (покрывает 0%-вырожд.)

# LLM two-pass
REVIEWER_ENABLED = True

# Журнал
JOURNAL_DB    = "journal/journal.db"
JOURNAL_JSONL = "journal/journal.jsonl"
