# =========================
# cmc.py — BNB HACK Trading Agent
# Слой контекста CoinMarketCap (спец-приз "Best use of CMC Data").
#
# На Basic-тарифе OHLCV недоступен (см. bsc_data.py — свечи берём с
# GeckoTerminal). CMC используем для:
#   - спот-котировок и 24h-изменения (quotes/latest — подтверждён рабочим);
#   - trending / listings → выбор и фильтр торговой вселенной;
#   - global-metrics → макро-контекст для LLM-агента (agent_brain);
#   - sanity cross-check цены против GeckoTerminal.
#
# dex_ohlcv() — заглушка: переключим свечи на CMC DEX API, когда он починится
# (сейчас /v4/dex/* отдаёт 500 на стороне CMC).
# =========================
from __future__ import annotations

import os
import ssl
from typing import Dict, List, Optional

import aiohttp

CMC_BASE = "https://pro-api.coinmarketcap.com"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

_TIMEOUT = aiohttp.ClientTimeout(total=15)


class CMCError(Exception):
    pass


class CMC:
    """Тонкий async-клиент CMC Pro. Ключ — из env CMC_API_KEY."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CMC_API_KEY", "")
        if not self.api_key:
            raise CMCError("CMC_API_KEY не задан (env)")

    async def _get(self, path: str, params: Optional[Dict] = None) -> Dict:
        headers = {"X-CMC_PRO_API_KEY": self.api_key, "Accept": "application/json"}
        connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
        async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector) as s:
            async with s.get(f"{CMC_BASE}{path}", params=params, headers=headers) as r:
                data = await r.json()
        st = data.get("status", {})
        if st.get("error_code") not in (0, "0", None):
            raise CMCError(f"{path} → [{st.get('error_code')}] {st.get('error_message')}")
        return data

    # ── Спот-котировки (подтверждено рабочим на Basic) ────
    async def quote(self, symbol: str) -> Dict:
        """{price, percent_change_24h, volume_24h, market_cap} для символа."""
        d = await self._get("/v2/cryptocurrency/quotes/latest",
                            params={"symbol": symbol})
        arr = d.get("data", {}).get(symbol) or []
        if not arr:
            raise CMCError(f"нет данных по {symbol}")
        q = arr[0]["quote"]["USD"]
        return {
            "symbol": symbol,
            "price": q.get("price"),
            "change_24h": q.get("percent_change_24h"),
            "volume_24h": q.get("volume_24h"),
            "market_cap": q.get("market_cap"),
            "updated": q.get("last_updated"),
        }

    async def quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        """Батч-котировки для списка символов (1 запрос)."""
        if not symbols:
            return {}
        d = await self._get("/v2/cryptocurrency/quotes/latest",
                            params={"symbol": ",".join(symbols)})
        out: Dict[str, Dict] = {}
        for sym, arr in (d.get("data") or {}).items():
            if arr:
                q = arr[0]["quote"]["USD"]
                out[sym] = {"price": q.get("price"),
                            "change_24h": q.get("percent_change_24h"),
                            "volume_24h": q.get("volume_24h"),
                            "market_cap": q.get("market_cap")}
        return out

    # ── Макро-контекст для LLM ────────────────────────────
    async def global_metrics(self) -> Dict:
        """Глобальные метрики рынка (BTC dominance, total mcap). Best-effort."""
        try:
            d = await self._get("/v1/global-metrics/quotes/latest")
            q = d.get("data", {}).get("quote", {}).get("USD", {})
            return {
                "btc_dominance": d.get("data", {}).get("btc_dominance"),
                "total_market_cap": q.get("total_market_cap"),
                "total_volume_24h": q.get("total_volume_24h"),
            }
        except CMCError:
            return {}

    # ── Trending для выбора вселенной (best-effort — может требовать тариф) ──
    async def trending(self, limit: int = 10) -> List[Dict]:
        for path in ("/v1/cryptocurrency/trending/latest",
                     "/v1/cryptocurrency/listings/latest"):
            try:
                d = await self._get(path, params={"limit": limit})
                rows = d.get("data") or []
                return [{"symbol": r.get("symbol"), "name": r.get("name"),
                         "rank": r.get("cmc_rank")} for r in rows][:limit]
            except CMCError:
                continue
        return []

    # ── Резолв символ → CMC id + BEP20-адрес (для token_registry) ──
    async def bsc_contracts(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        {symbol: {"id":int, "bsc_address":str}} для символов, у которых есть
        контракт на BNB Smart Chain (BEP20). При коллизии тикеров берём
        вариант с BEP20-адресом (и наибольшим rank).
        """
        if not symbols:
            return {}
        d = await self._get("/v2/cryptocurrency/info",
                            params={"symbol": ",".join(symbols)})
        out: Dict[str, Dict] = {}
        for sym, arr in (d.get("data") or {}).items():
            cands = arr if isinstance(arr, list) else [arr]
            best = None
            for c in cands:
                bsc = _find_bsc_address(c)
                if bsc:
                    rank = c.get("cmc_rank") or 10**9
                    if best is None or rank < best[2]:
                        best = (c.get("id"), bsc, rank)
            if best:
                out[sym] = {"id": best[0], "bsc_address": best[1]}
        return out

    # ── CMC DEX OHLCV — предпочтительный источник свечей, когда заработает ──
    async def dex_ohlcv(self, *args, **kwargs):
        raise CMCError("CMC DEX API недоступен (500). Свечи — через bsc_data.py")


def _find_bsc_address(info: Dict) -> Optional[str]:
    """Достаёт BEP20-адрес из CMC info (поле platform или список contract_address)."""
    # вариант 1: одиночный platform
    pf = info.get("platform") or {}
    if pf and "bnb" in str(pf.get("name", "")).lower() and info.get("contract_address"):
        ca = info.get("contract_address")
        if isinstance(ca, str):
            return ca
    # вариант 2: список contract_address по сетям
    ca = info.get("contract_address")
    if isinstance(ca, list):
        for x in ca:
            p = (x.get("platform") or {})
            name = str(p.get("name", "")).lower()
            if "bnb smart chain" in name or "bep20" in name:
                return x.get("contract_address")
    return None
