# =========================
# token_registry.py — BNB HACK Trading Agent
# Реестр торговой вселенной: symbol → {cmc_id, bsc_address, pool}.
#
# Источник eligible-списка — config.ELIGIBLE_SYMBOLS (149 BEP-20 с CMC).
# Резолв: CMC info → BEP20-адрес + cmc_id; GeckoTerminal → топ-пул.
# Стейблы/пеги (config.STABLE_SYMBOLS) исключаются. Результат кэшируется в
# token_registry.json (сборка медленная и one-time; терпима к пропускам).
#
# Запуск сборки:  python3 token_registry.py
# =========================
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import config.settings as C
from cmc.cmc_client import CMC, CMCError
from marketdata.gt_feed import GeckoTerminalFeed, BscDataError


@dataclass
class TokenEntry:
    symbol: str
    cmc_id: int
    address: str          # BEP20 contract
    pool: Optional[str] = None    # топ-пул PancakeSwap (GeckoTerminal)
    pool_name: Optional[str] = None


def tradable_symbols() -> List[str]:
    """Eligible минус стейблы/пеги, дедуп, без не-ASCII мусора-тикеров."""
    seen, out = set(), []
    for s in C.ELIGIBLE_SYMBOLS:
        if s in C.STABLE_SYMBOLS or s in seen:
            continue
        if not s.isascii():            # напр. '币安人生' — не резолвится по API
            continue
        seen.add(s)
        out.append(s)
    return out


class TokenRegistry:
    def __init__(self, path: str = None):
        self.path = path or C.TOKEN_REGISTRY_FILE
        self.entries: Dict[str, TokenEntry] = {}

    # ── Загрузка кэша ─────────────────────────────────────
    def load(self) -> "TokenRegistry":
        if os.path.exists(self.path):
            with open(self.path) as f:
                blob = json.load(f)
            self.entries = {s: TokenEntry(**d) for s, d in blob.items()}
        return self

    def _save(self) -> None:
        with open(self.path, "w") as f:
            json.dump({s: asdict(e) for s, e in self.entries.items()}, f, indent=2)

    def addresses(self) -> Dict[str, str]:
        return {s: e.address for s, e in self.entries.items()}

    def with_pools(self) -> List[TokenEntry]:
        return [e for e in self.entries.values() if e.pool]

    # ── Сборка реестра ────────────────────────────────────
    async def build(self, *, batch: int = 25, resolve_pools: bool = True) -> "TokenRegistry":
        cmc = CMC()
        syms = tradable_symbols()
        print(f"[REGISTRY] резолв {len(syms)} торговых символов через CMC...")

        # 1) symbol → cmc_id + BEP20-адрес (батчами)
        resolved: Dict[str, Dict] = {}
        for i in range(0, len(syms), batch):
            chunk = syms[i:i + batch]
            try:
                resolved.update(await cmc.bsc_contracts(chunk))
            except CMCError as e:
                print(f"[REGISTRY] CMC info батч {i//batch} ошибка: {e}")
        print(f"[REGISTRY] с BEP20-адресом: {len(resolved)}/{len(syms)}")

        for sym, info in resolved.items():
            self.entries[sym] = TokenEntry(symbol=sym, cmc_id=info["id"],
                                           address=info["bsc_address"])

        # 2) адрес → топ-пул PancakeSwap (GeckoTerminal, с троттлингом)
        if resolve_pools:
            feed = GeckoTerminalFeed("bsc")
            ok = 0
            for e in list(self.entries.values()):
                try:
                    p = await feed.top_pool_for_token(e.address)
                    e.pool, e.pool_name = p["address"], p["name"]
                    ok += 1
                except BscDataError:
                    pass    # нет пула / неликвид → останется без pool, отсеется скринером
            print(f"[REGISTRY] с пулом на BSC: {ok}/{len(self.entries)}")

        self._save()
        print(f"[REGISTRY] сохранено в {self.path}")
        return self


if __name__ == "__main__":
    async def main():
        reg = await TokenRegistry().build()
        withp = reg.with_pools()
        print(f"\nИтог: {len(reg.entries)} токенов, {len(withp)} с пулом.")
        for e in withp[:15]:
            print(f"  {e.symbol:10} id={e.cmc_id:<7} {e.address[:12]}…  pool={e.pool_name}")

    asyncio.run(main())
