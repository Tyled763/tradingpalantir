# =========================
# intelligence/token_risk_firewall.py — TradingPalantir
# Реплика `Verify New Token Safety` + `detect token liquidity decay` (лайт):
# (1) allowlist-гейт (только eligible 128), (2) honeypot/чек через `twak risk`,
# (3) минимальная ликвидность пула (GeckoTerminal reserve_usd).
# Результат кэшируется на FIREWALL_CACHE_H. Fail → монета не торгуется.
# =========================
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

import config as C
from execution.twak_adapter import TwakExec, TwakError
from intelligence.evidence import EvidenceItem

_CACHE_FILE = "config/firewall_cache.json"


class TokenRiskFirewall:
    def __init__(self, twak: Optional[TwakExec] = None, feed=None):
        self.twak = twak
        self.feed = feed     # GeckoTerminalFeed (для ликвидности пула)
        self._cache: Dict[str, Dict] = {}
        if os.path.exists(_CACHE_FILE):
            try:
                with open(_CACHE_FILE) as f:
                    self._cache = json.load(f)
            except (OSError, json.JSONDecodeError):
                self._cache = {}

    def _save(self) -> None:
        try:
            with open(_CACHE_FILE, "w") as f:
                json.dump(self._cache, f)
        except OSError:
            pass

    async def check(self, symbol: str, address: str, pool: Optional[str] = None) -> Dict:
        """{status: approved|approved_small|rejected, reasons:[], evidence}"""
        key = address.lower()
        cached = self._cache.get(key)
        if cached and time.time() - cached.get("ts", 0) < C.FIREWALL_CACHE_H * 3600:
            return cached["result"]

        reasons, status = [], "approved"

        # 1) ликвидность пула (GT)
        liq = None
        if self.feed is not None:
            try:
                p = await self.feed.top_pool_for_token(address)
                liq = float(p.get("reserve_usd") or 0) or None
            except Exception:
                pass
        if liq is not None and liq < C.FIREWALL_MIN_LIQUIDITY_USD:
            status = "rejected"
            reasons.append(f"ликвидность пула ${liq:,.0f} < ${C.FIREWALL_MIN_LIQUIDITY_USD:,}")

        # 2) twak risk (honeypot/контракт-чек) — если доступен
        if self.twak is not None and status != "rejected":
            try:
                r = await self.twak.token_risk(address)
                flag = str(r.get("risk") or r.get("level") or "").lower()
                if flag in ("high", "danger", "honeypot"):
                    status = "rejected"
                    reasons.append(f"twak risk: {flag}")
                elif flag in ("medium", "warning"):
                    status = "approved_small"
                    reasons.append(f"twak risk: {flag} → только малый размер")
            except (TwakError, Exception):
                reasons.append("twak risk недоступен (partial)")

        result = {
            "status": status, "reasons": reasons, "liquidity_usd": liq,
            "evidence": EvidenceItem(
                source_skill="Verify New Token Safety", pipeline="firewall",
                category="onchain_token_safety", asset_symbol=symbol,
                status="partial" if "недоступен (partial)" in " ".join(reasons) else "complete",
                risk_impact="avoid" if status == "rejected" else "hold",
                summary=f"{symbol}: {status} {reasons}").to_dict(),
        }
        self._cache[key] = {"ts": time.time(), "result": result}
        self._save()
        return result
