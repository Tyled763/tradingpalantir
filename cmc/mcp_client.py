# =========================
# cmc_mcp.py — BNB HACK Trading Agent
# Клиент официального CMC MCP-сервера (CMC AI Agent Hub) — слой «CMC сигналов»
# для ПОДТВЕРЖДЕНИЯ (первичный сигнал всегда из стратегии пользователя).
#
# Эндпоинт: https://mcp.coinmarketcap.com/mcp  (header X-CMC-MCP-API-KEY).
# Stateless JSON-RPC поверх HTTP (без MCP-SDK). Используем НЕ-TA инструменты:
#   - get_crypto_latest_news      → новостной фон по монете (для Claude)
#   - trending_crypto_narratives  → объём + соц-баззу (socialKeywords/авторы)
#   - get_global_metrics_latest / get_global_crypto_derivatives_metrics /
#     get_upcoming_macro_events   → macro_liquidity_monitor (risk-on/off)
#   - search_cryptos              → symbol → CMC id
# RSI/MACD (get_crypto_technical_analysis) НЕ используем — место под кастомный
# индикатор пользователя.
# =========================
from __future__ import annotations

import json
import os
import ssl
from typing import Any, Dict, List, Optional

import aiohttp

MCP_URL = "https://mcp.coinmarketcap.com/mcp"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
_TIMEOUT = aiohttp.ClientTimeout(total=25)


class CMCMCPError(Exception):
    pass


class CMCMcp:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CMC_API_KEY", "")
        if not self.api_key:
            raise CMCMCPError("CMC_API_KEY не задан (env)")
        self._id = 0

    async def _rpc(self, method: str, params: Dict) -> Dict:
        self._id += 1
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "X-CMC-MCP-API-KEY": self.api_key,
        }
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        connector = aiohttp.TCPConnector(ssl=_SSL_CTX)
        async with aiohttp.ClientSession(timeout=_TIMEOUT, connector=connector) as s:
            async with s.post(MCP_URL, json=body, headers=headers) as r:
                raw = await r.text()
        return _parse_rpc(raw)

    async def call_tool(self, name: str, arguments: Optional[Dict] = None) -> Any:
        """Вызов MCP-инструмента. Возвращает распарсенный JSON из content[0].text."""
        res = await self._rpc("tools/call",
                              {"name": name, "arguments": arguments or {}})
        if "error" in res:
            raise CMCMCPError(f"{name}: {res['error']}")
        content = (res.get("result", {}) or {}).get("content", [])
        if not content:
            return None
        text = content[0].get("text", "")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    # ── Высокоуровневые хелперы ───────────────────────────
    async def search_id(self, query: str) -> Optional[int]:
        data = await self.call_tool("search_cryptos", {"query": query})
        rows = _rows(data)
        return int(rows[0].get("id")) if rows and rows[0].get("id") else None

    async def latest_news(self, cmc_id: int, limit: int = 5) -> List[Dict]:
        """Список последних новостей по монете: title/description/url/publishedAt/quality."""
        data = await self.call_tool("get_crypto_latest_news", {"id": str(cmc_id)})
        out = []
        for row in _rows(data)[:limit]:
            out.append({k: row.get(k) for k in
                        ("title", "description", "url", "publishedAt", "quality")})
        return out

    async def trending_narratives(self) -> List[Dict]:
        """Категории в моменте: объём, изменение объёма, соц-кейворды, топ-монеты."""
        data = await self.call_tool("trending_crypto_narratives", {})
        cats = []
        rows = _rows(data, key="categoryList")
        for r in rows:
            cats.append({
                "name": r.get("categoryName"),
                "volume24h": r.get("volume24h"),
                "volume_change_24h": r.get("volumeChangePercentage24h"),
                "mcap_change_24h": r.get("marketCapChangePercentage24h"),
                "social_keywords": r.get("socialKeywords"),
                "social_authors": r.get("socialKeywordUniqueAuthorCount"),
                "top_coins": r.get("topCoinList"),
            })
        return cats

    async def global_metrics(self) -> Dict:
        return await self.call_tool("get_global_metrics_latest", {}) or {}

    async def derivatives_metrics(self) -> Dict:
        return await self.call_tool("get_global_crypto_derivatives_metrics", {}) or {}

    async def macro_events(self) -> Any:
        return await self.call_tool("get_upcoming_macro_events", {})

    # ── Эквивалент скилла macro_liquidity_monitor ─────────
    async def macro_liquidity_monitor(self) -> Dict:
        """
        Собирает global + derivatives + macro_events → режим рынка.
        Возвращает {regime: risk_on|neutral|risk_off, factors:{...}}.
        Эвристика устойчива к отсутствию полей (по умолчанию neutral).
        """
        factors: Dict[str, Any] = {}
        score = 0
        try:
            g = await self.global_metrics()
            chg = _deep_num(g, ("market_size", "total_crypto_market_cap_usd",
                                "percent_change", "24h"))
            factors["total_mcap_change_24h"] = chg
            if chg is not None:
                score += 1 if chg > 1.0 else (-1 if chg < -2.0 else 0)
        except CMCMCPError:
            pass
        try:
            d = await self.derivatives_metrics()
            oi_chg = _deep_num(d, ("totalOpenInterest", "percentage_change_24h"))
            factors["derivatives_oi_change_24h"] = oi_chg
            if oi_chg is not None:
                score += 1 if oi_chg > 0 else -1
        except CMCMCPError:
            pass

        regime = "risk_on" if score >= 1 else ("risk_off" if score <= -1 else "neutral")
        return {"regime": regime, "score": score, "factors": factors}


# ── Парсинг ───────────────────────────────────────────────
def _parse_rpc(raw: str) -> Dict:
    """JSON или SSE (text/event-stream). Возвращает первый объект с result/error."""
    for line in raw.splitlines():
        line = line[6:].strip() if line.startswith("data: ") else line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj):
            return obj
    # один цельный JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise CMCMCPError(f"не разобрал ответ MCP: {raw[:160]}")


def _rows(data: Any, key: Optional[str] = None) -> List[Dict]:
    """CMC MCP часто отдаёт {headers:[...], rows:[[...]]} → список dict."""
    if data is None:
        return []
    if key and isinstance(data, dict) and key in data:
        data = data[key]
    if isinstance(data, dict) and "headers" in data and "rows" in data:
        hdr = data["headers"]
        return [dict(zip(hdr, row)) for row in data["rows"]]
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _deep_num(d: Any, path: tuple) -> Optional[float]:
    """Пробует достать число по пути ключей; None если нет."""
    cur = d
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    try:
        return float(str(cur).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
