# =========================
# twak_exec.py — BNB HACK Trading Agent
# Слой исполнения на BSC через Trust Wallet Agent Kit (`twak` CLI).
# Заменяет okx_trade.py / okx_private_ws.py.
#
# Все методы async, дёргают `twak <cmd> ... --json` через subprocess и
# парсят JSON. Самокастоди: подпись делает twak локально из своего кошелька.
#
# ВНИМАНИЕ: формы JSON-ответов swap/automate ещё НЕ верифицированы на live
# (нужны TWAK_ACCESS_ID/TWAK_HMAC_SECRET). Помечено TODO(verify-poc).
# =========================
from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any, Dict, List, Optional


class TwakError(Exception):
    """Ошибка вызова twak CLI (ненулевой код или error-envelope в JSON)."""


def _resolve_twak_bin() -> str:
    """Путь к бинарю twak. Приоритет: env TWAK_BIN → PATH → nvm-дефолт (этот Mac)."""
    env = os.environ.get("TWAK_BIN")
    if env:
        return env
    on_path = shutil.which("twak")
    if on_path:
        return on_path
    # nvm-инсталляция на dev-машине (см. memory bnbhack-goal)
    home = os.path.expanduser("~")
    cand = f"{home}/.nvm/versions/node/v24.16.0/bin/twak"
    if os.path.exists(cand):
        return cand
    return "twak"  # последняя надежда — пусть subprocess сам ругнётся


class TwakExec:
    """
    Тонкая обёртка над `twak` CLI для спот-торговли на BSC (PancakeSwap)
    и сопутствующих операций хакатона (compete, erc8004, token-risk).

    chain по умолчанию — "bsc". Для testnet см. NETWORK в config (twak сам
    разруливает по chain-key; testnet-ключ уточняется в PoC).
    """

    def __init__(self, *, chain: str = "bsc",
                 wallet_password: Optional[str] = None,
                 default_slippage: float = 1.0):
        self._bin      = _resolve_twak_bin()
        self.chain     = chain
        self._password = wallet_password or os.environ.get("TWAK_WALLET_PASSWORD")
        self.slippage  = default_slippage

    # ── Низкоуровневый вызов ──────────────────────────────
    async def _run(self, *args: str, timeout: float = 60.0,
                   need_password: bool = False) -> Any:
        """
        Запускает `twak <args> --json`, возвращает распарсенный JSON.
        Бросает TwakError при ненулевом коде или error-envelope.
        """
        argv: List[str] = [self._bin, *args, "--json", "--no-analytics"]
        if need_password and self._password:
            argv += ["--password", self._password]

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as e:
            proc.kill()
            raise TwakError(f"timeout {timeout}s: twak {' '.join(args)}") from e

        stdout = out.decode().strip()
        stderr = err.decode().strip()

        # twak с --json печатает JSON в stdout даже на ошибках (error-envelope)
        data: Any = None
        if stdout:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError:
                # иногда баннер/мусор перед JSON — вычленяем последний JSON-блок
                data = _extract_json(stdout)

        if isinstance(data, dict) and (data.get("error") or data.get("errorCode")):
            raise TwakError(f"twak {' '.join(args)} → {data.get('error')} "
                            f"[{data.get('errorCode')}]")
        if proc.returncode != 0 and data is None:
            raise TwakError(f"twak {' '.join(args)} exit={proc.returncode} "
                            f"stderr={stderr or '(empty)'}")
        return data

    # ── Auth / wallet (read-only проверки) ────────────────
    async def auth_status(self) -> Dict:
        return await self._run("auth", "status")

    async def wallet_address(self) -> Dict:
        return await self._run("wallet", "address", "--chain", self.chain)

    async def portfolio(self) -> Dict:
        return await self._run("wallet", "portfolio")

    async def balance(self, token: Optional[str] = None) -> Dict:
        args = ["wallet", "balance", "--chain", self.chain]
        return await self._run(*args)

    # ── Хакатон ───────────────────────────────────────────
    async def compete_status(self) -> Dict:
        return await self._run("compete", "status")

    async def compete_register(self) -> Dict:
        return await self._run("compete", "register", need_password=True)

    # ── Безопасность токена (honeypot/rug перед входом) ────
    async def token_risk(self, asset_id: str) -> Dict:
        return await self._run("risk", asset_id)

    # ── Котировка / своп ──────────────────────────────────
    async def quote(self, amount: float, frm: str, to: str) -> Dict:
        """Котировка без исполнения (--quote-only). Всегда вызывать перед swap."""
        return await self._run(
            "swap", str(amount), frm, to,
            "--chain", self.chain,
            "--slippage", str(self.slippage),
            "--quote-only",
        )

    async def swap(self, amount: float, frm: str, to: str,
                   *, slippage: Optional[float] = None) -> Dict:
        """
        Исполнить своп amount frm → to на self.chain. Требует пароль кошелька.
        Вход в позицию: swap(usdt_amount, "USDT", TOKEN).
        Выход: swap(token_qty, TOKEN, "USDT").
        TODO(verify-poc): зафиксировать форму JSON (txHash, executedPrice, received).
        """
        return await self._run(
            "swap", str(amount), frm, to,
            "--chain", self.chain,
            "--slippage", str(slippage if slippage is not None else self.slippage),
            need_password=True,
        )

    # ── Bracket: TP/SL как limit-automations ──────────────
    # automate add: --price трекает USD-цену не-стейбл токена (см. automations.md,
    # пример "sell BNB above 700"). Для нашего кейса (держим TOKEN, продаём в USDT):
    #   TP → --condition above --price <tp>
    #   SL → --condition below --price <sl>
    # TODO(verify-poc): подтвердить, что price трекает source-токен при sell-в-стейбл.
    async def place_limit(self, *, frm: str, to: str, amount: float,
                          price: float, condition: str,
                          max_runs: int = 1) -> Dict:
        if condition not in ("above", "below"):
            raise TwakError(f"condition must be above/below, got {condition!r}")
        return await self._run(
            "automate", "add",
            "--from", frm, "--to", to,
            "--chain", self.chain,
            "--amount", str(amount),
            "--price", str(price),
            "--condition", condition,
            "--max-runs", str(max_runs),
        )

    async def place_tp(self, token: str, qty: float, tp_px: float) -> Dict:
        return await self.place_limit(frm=token, to="USDT", amount=qty,
                                      price=tp_px, condition="above")

    async def place_sl(self, token: str, qty: float, sl_px: float) -> Dict:
        return await self.place_limit(frm=token, to="USDT", amount=qty,
                                      price=sl_px, condition="below")

    async def list_automations(self) -> List[Dict]:
        data = await self._run("automate", "list")
        if isinstance(data, dict):
            return data.get("automations") or data.get("items") or []
        return data or []

    async def delete_automation(self, automation_id: str) -> Dict:
        return await self._run("automate", "delete", automation_id)

    # ── ERC-8004 identity (BNB-стек, спец-приз) ───────────
    async def erc8004_register(self, uri: str) -> Dict:
        """Минт ERC-8004 identity NFT на BSC. uri — https:// или ipfs:// на agent-card."""
        return await self._run(
            "erc8004", "register",
            "--uri", uri, "--chain", "bsc",
            need_password=True,
        )

    async def erc8004_show(self, agent_id: str) -> Dict:
        return await self._run("erc8004", "show", agent_id, "--chain", "bsc")


def _extract_json(text: str) -> Any:
    """Достаёт последний валидный JSON-объект/массив из замусоренного stdout."""
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end   = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    return None
