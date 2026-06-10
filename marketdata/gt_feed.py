# =========================
# bsc_data.py — BNB HACK Trading Agent
# Слой OHLCV-данных для BSC-пар PancakeSwap.
#
# Боевой источник свечей — GeckoTerminal (бесплатно, без ключа): отдаёт
# настоящий OHLCV [ts, open, high, low, close, volume] по on-chain пулам.
# CMC на Basic-тарифе не отдаёт OHLCV (см. cmc.py — там спот/контекст).
#
# Бары возвращаются в формате, который ест BarProcessor._process_bar:
#   {time: pd.Timestamp(UTC), open, high, low, close, vol, vol_usdt}
#   vol_usdt — объём в USD (для VWAP, как r[7] у OKX); vol — оценка базового.
#
# Таймфреймы: 5m, 15m нативно; 30m ресемплится из 15m; 1H нативно (hour/1).
# =========================
from __future__ import annotations

import asyncio
import ssl
import time
from typing import Dict, List, Optional

import aiohttp
import pandas as pd

GT_BASE = "https://api.geckoterminal.com/api/v2"

# SSL как в остальном проекте (macOS Python 3.13)
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Наш ТФ → (GeckoTerminal endpoint, aggregate). 30m нет в GT → ресемпл из 15m.
_GT_TF = {
    "5m":  ("minute", 5),
    "15m": ("minute", 15),
    "1H":  ("hour", 1),
}
_GT_MAX = 1000          # потолок свечей за один запрос GeckoTerminal


class BscDataError(Exception):
    pass


class GeckoTerminalFeed:
    """
    OHLCV по пулу PancakeSwap на BSC. pool_address — адрес пула (не токена).
    network фиксирован "bsc".

    Бесплатный GeckoTerminal ~30 req/min → встроенный троттлинг (min-interval)
    + ретрай на 429 с бэкоффом + кэш резолва пулов.
    """

    def __init__(self, network: str = "bsc", min_interval: float = 2.2):
        self.network = network
        self._min_interval = min_interval      # ~27 req/min, под лимитом 30
        self._last_call = 0.0
        self._lock = asyncio.Lock()
        self._pool_cache: Dict[str, Dict] = {}

    async def _get(self, path: str, params: Optional[Dict] = None,
                   _retries: int = 3) -> Dict:
        # сериализуем вызовы + выдерживаем min-interval (rate-limit гард)
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

        connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
        async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector) as s:
            async with s.get(f"{GT_BASE}{path}", params=params,
                             headers={"Accept": "application/json"}) as r:
                if r.status == 429 and _retries > 0:
                    await asyncio.sleep((4 - _retries) * 3 + 3)   # 3,6,9s бэкофф
                    return await self._get(path, params, _retries - 1)
                if r.status != 200:
                    body = (await r.text())[:200]
                    raise BscDataError(f"GET {path} → HTTP {r.status}: {body}")
                return await r.json()

    # ── Поиск пула: топ-пул токена против USDT/WBNB ───────
    async def top_pool_for_token(self, token_address: str) -> Dict:
        """Самый ликвидный пул токена на BSC (с кэшем). Возвращает {address, name}."""
        key = token_address.lower()
        if key in self._pool_cache:
            return self._pool_cache[key]
        data = await self._get(f"/networks/{self.network}/tokens/{token_address}/pools",
                               params={"page": 1})
        arr = data.get("data") or []
        if not arr:
            raise BscDataError(f"нет пулов для токена {token_address}")
        a = arr[0]
        pool = {"address": a["attributes"]["address"],
                "name": a["attributes"].get("name", ""),
                "reserve_usd": a["attributes"].get("reserve_in_usd")}
        self._pool_cache[key] = pool
        return pool

    # ── Сырой OHLCV нативного ТФ GeckoTerminal ────────────
    async def _raw_ohlcv(self, pool: str, gt_unit: str, aggregate: int,
                         limit: int, token: Optional[str] = None) -> List[List[float]]:
        """
        token: адрес целевого токена → цена денонимируется в нём (в USD).
        Без него GeckoTerminal отдаёт цену базового токена пула (может быть USDT).
        """
        params = {"aggregate": aggregate, "limit": min(limit, _GT_MAX),
                  "currency": "usd"}
        if token:
            params["token"] = token
        data = await self._get(
            f"/networks/{self.network}/pools/{pool}/ohlcv/{gt_unit}",
            params=params,
        )
        ol = (data.get("data", {}).get("attributes", {}).get("ohlcv_list")) or []
        # GeckoTerminal отдаёт от новых к старым → переворачиваем в хронологию
        return list(reversed(ol))

    # ── Бары в формате BarProcessor ───────────────────────
    async def bars(self, pool: str, tf: str, limit: int = 720,
                   token: Optional[str] = None) -> List[Dict]:
        """
        Список баров для ТФ tf (5m/15m/30m/1H), от старых к новым.
        30m строится ресемплом из 15m (GeckoTerminal не имеет 30m).
        token: адрес целевого торгового токена (цена в его USD-номинале).
        """
        if tf == "30m":
            raw15 = await self._raw_ohlcv(pool, "minute", 15,
                                          min(limit * 2, _GT_MAX), token)
            rows = [_to_bar(c) for c in raw15]
            return _resample(rows, "30min")[-limit:]

        if tf not in _GT_TF:
            raise BscDataError(f"неизвестный ТФ {tf}")
        gt_unit, agg = _GT_TF[tf]
        raw = await self._raw_ohlcv(pool, gt_unit, agg, limit, token)
        return [_to_bar(c) for c in raw]

    # ── Бары по адресу токена (сам резолвит пул + ориентацию цены) ──
    async def bars_for_token(self, token_address: str, tf: str,
                             limit: int = 720,
                             pool: Optional[str] = None) -> List[Dict]:
        """
        Удобный вход: даём адрес торгового токена → получаем его свечи в USD.
        Если pool не задан — берём самый ликвидный пул токена.
        """
        if pool is None:
            pool = (await self.top_pool_for_token(token_address))["address"]
        return await self.bars(pool, tf, limit, token=token_address)


def _to_bar(c: List[float]) -> Dict:
    """[ts, open, high, low, close, volume_usd] → бар BarProcessor."""
    ts, o, h, l, cl, vol_usd = c[0], c[1], c[2], c[3], c[4], c[5]
    close = float(cl) or 1.0
    return {
        "time":     pd.Timestamp(int(ts), unit="s", tz="UTC"),
        "open":     float(o),
        "high":     float(h),
        "low":      float(l),
        "close":    float(cl),
        "vol":      float(vol_usd) / close,   # оценка объёма в базовом токене
        "vol_usdt": float(vol_usd),           # объём в USD → для VWAP
    }


def _resample(rows: List[Dict], rule: str) -> List[Dict]:
    """Ресемпл баров в более крупный ТФ (например 15m → 30min)."""
    if not rows:
        return []
    df = pd.DataFrame(rows).set_index("time").sort_index()
    agg = df.resample(rule, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "vol": "sum", "vol_usdt": "sum",
    }).dropna(subset=["open"])
    out = []
    for ts, r in agg.iterrows():
        out.append({
            "time": ts, "open": r["open"], "high": r["high"], "low": r["low"],
            "close": r["close"], "vol": r["vol"], "vol_usdt": r["vol_usdt"],
        })
    return out


async def warmup_df(feed: GeckoTerminalFeed, pool: str, tf: str,
                    limit: int = 720, token: Optional[str] = None) -> pd.DataFrame:
    """DataFrame баров для BarProcessor.warmup_from_df()."""
    rows = await feed.bars(pool, tf, limit, token=token)
    return pd.DataFrame(rows)


async def warmup_df_for_token(feed: GeckoTerminalFeed, token_address: str,
                              tf: str, limit: int = 720,
                              pool: Optional[str] = None) -> pd.DataFrame:
    """DataFrame баров целевого токена (цена в USD) для BarProcessor."""
    rows = await feed.bars_for_token(token_address, tf, limit, pool=pool)
    return pd.DataFrame(rows)
