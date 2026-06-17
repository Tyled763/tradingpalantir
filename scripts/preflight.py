# =========================
# scripts/preflight.py — TradingPalantir
# READ-ONLY проверка готовности к live-окну (22-28 июня).
# Ничего не торгует, не подписывает, не меняет конфиг — только читает:
#   1. config: DRY_RUN, LIVE_WINDOW, риск-параметры, fallback
#   2. systemd-сервис (если запущено на VPS)
#   3. twak (read-only): auth, кошелёк, портфель, баланс USDT/BNB, compete status
#   4. journal: сделок сегодня, открытые позиции, последние события
# Вывод: [ OK ] / [WARN] / [FAIL] по каждому пункту + итоговый вердикт.
#
# Запуск (на VPS, где .env и twak настроены):
#   cd /root/tradingpalantir && python3 -m scripts.preflight
# =========================
from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import config as C
from execution.twak_adapter import TwakExec, TwakError
from journal.trade_journal import TradeJournal
from risk.core import PositionRegistry, RuleBook
from risk.daily_trade_monitor import DailyTradeMonitor, in_live_window

# ── Пороги (мягкие, под фикс $50-счёт + неделя свопов) ──────
GAS_MIN_BNB      = 0.010    # комфортно (~неделя свопов); ниже — WARN
GAS_CRIT_BNB     = 0.004    # критично мало газа → FAIL
INSCOPE_MIN_USD  = 1.0      # правило: портфель ≤ $1 = 0% за час
QUOTE_RESERVE    = C.FALLBACK_NOTIONAL + 1.0   # свободного USDT хотя бы на fallback

OK, WARN, FAIL = "[ OK ]", "[WARN]", "[FAIL]"
_results: list[tuple[str, str]] = []


def report(status: str, line: str) -> None:
    _results.append((status, line))
    print(f"{status}  {line}")


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 46 - len(title)))


def _num(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).split()[0])
    except (TypeError, ValueError, IndexError):
        return default


def _asset_usd(a: Any) -> float:
    if isinstance(a, dict):
        return _num(a.get("usd") or a.get("usdValue") or a.get("valueUsd")
                    or a.get("value") or 0.0)
    return 0.0


def _portfolio_total(p: Any) -> float:
    """Сумма USD из портфеля — поддерживает list[asset] и dict{totalUsd|assets}."""
    if isinstance(p, dict):
        t = p.get("totalUsd") or p.get("total") or p.get("totalUSD")
        if t is not None:
            return _num(t)
        for key in ("assets", "balances", "tokens", "items"):
            if isinstance(p.get(key), list):
                return sum(_asset_usd(a) for a in p[key])
        return 0.0
    if isinstance(p, list):
        return sum(_asset_usd(a) for a in p)
    return 0.0


def _find_asset(data: Any, symbol: str) -> Optional[dict]:
    """Best-effort поиск {amount, usd} токена в разных формах ответа twak."""
    sym = symbol.upper()
    if isinstance(data, dict):
        for key in ("assets", "balances", "tokens", "items"):
            seq = data.get(key)
            if isinstance(seq, list):
                for a in seq:
                    if isinstance(a, dict) and str(a.get("symbol", a.get("asset", ""))).upper() == sym:
                        return a
            if isinstance(seq, dict) and sym in {k.upper() for k in seq}:
                for k, v in seq.items():
                    if k.upper() == sym:
                        return v if isinstance(v, dict) else {"amount": v}
    if isinstance(data, list):
        for a in data:
            if isinstance(a, dict) and str(a.get("symbol", a.get("asset", ""))).upper() == sym:
                return a
    return None


def check_config() -> None:
    section("1. Config")
    live = not C.DRY_RUN
    if live:
        report(OK, "DRY_RUN = False → LIVE (реальные свопы через twak)")
    else:
        report(WARN, "DRY_RUN = True → PAPER. Для live-окна поставь False и рестартни сервис")
    report(OK, f"LIVE_WINDOW = {C.LIVE_WINDOW[0]} … {C.LIVE_WINDOW[1]}")
    in_win = in_live_window()
    report(OK if in_win else WARN,
           f"Сейчас {'ВНУТРИ' if in_win else 'вне'} live-окна (UTC {datetime.now(timezone.utc).date()})")
    try:
        r = RuleBook.load(C.RULES_FILE)
        report(OK, f"Риск/сделку ${r.max_risk_per_trade_usdt:g} · кап ноционала "
                   f"${r.max_position_notional_usdt:g} · max позиций {r.max_concurrent_positions} · "
                   f"one-per-symbol={r.one_position_per_symbol} · long_only={r.long_only} · "
                   f"{len(r.allowed_tokens)} allowlist-токенов")
    except Exception as e:
        report(WARN, f"не удалось прочитать rules.json: {e}")
    report(OK, f"Drawdown guard: {C.DD_DEFENSIVE_PCT}/{C.DD_BLOCK_PCT}/{C.DD_FLATTEN_PCT}% "
               f"(defensive/block/flatten)")
    report(OK, f"Fallback: ≥{C.DAILY_MIN_TRADES} сделка/день, round-trip ${C.FALLBACK_NOTIONAL}, "
               f"окно {C.FALLBACK_WINDOW_H}ч до конца дня UTC")


def check_service() -> None:
    section("2. systemd-сервис")
    if not shutil.which("systemctl"):
        report(WARN, "systemctl недоступен (не VPS?) — пропуск проверки сервиса")
        return
    svc = "tradingpalantir.service"
    try:
        active = subprocess.run(["systemctl", "is-active", svc],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        enabled = subprocess.run(["systemctl", "is-enabled", svc],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        report(OK if active == "active" else FAIL, f"{svc}: {active}")
        report(OK if enabled == "enabled" else WARN, f"автозапуск после ребута: {enabled}")
    except Exception as e:
        report(WARN, f"не удалось опросить сервис: {e}")


async def check_twak() -> None:
    section("3. twak (read-only)")
    twak = TwakExec(chain="bsc")

    try:
        await twak.auth_status()
        report(OK, "twak auth: ok")
    except TwakError as e:
        report(FAIL, f"twak auth провален — креды/CLI: {e}")
        return
    except FileNotFoundError:
        report(FAIL, "twak CLI не найден (npm i -g @trustwallet/cli или TWAK_BIN)")
        return

    try:
        addr = await twak.wallet_address()
        report(OK, f"wallet: {addr.get('address') or addr}")
    except TwakError as e:
        report(WARN, f"wallet address: {e}")

    # портфель → общий USD (правило: ≤ $1 = 0%)
    port: Any = None
    try:
        port = await twak.portfolio()
        total = _portfolio_total(port)
        st = OK if total > INSCOPE_MIN_USD else FAIL
        report(st, f"портфель: ${total:.2f}" +
                   ("" if total > INSCOPE_MIN_USD else f"  ≤ ${INSCOPE_MIN_USD} → не будет ранжироваться!"))
        if total == 0.0:
            print("    raw portfolio →", port)
    except TwakError as e:
        report(WARN, f"portfolio: {e}")

    # баланс USDT (свободный на входы + fallback) и BNB (газ) — из balance, иначе из портфеля
    try:
        try:
            bal: Any = await twak.balance()
        except TwakError:
            bal = port
        usdt = _find_asset(bal, C.QUOTE_CCY) or _find_asset(port, C.QUOTE_CCY)
        bnb = _find_asset(bal, "BNB") or _find_asset(port, "BNB")
        if usdt is not None:
            amt = _num(usdt.get("amount") or usdt.get("balance") or usdt)
            report(OK if amt >= QUOTE_RESERVE else WARN,
                   f"{C.QUOTE_CCY} свободно: {amt:.2f}" +
                   ("" if amt >= QUOTE_RESERVE else f"  < ${QUOTE_RESERVE:.0f} резерва под fallback"))
        else:
            report(WARN, f"{C.QUOTE_CCY} не найден в balance — проверь вручную (raw ниже)")
        if bnb is not None:
            gas = _num(bnb.get("amount") or bnb.get("balance") or bnb)
            st = OK if gas >= GAS_MIN_BNB else (FAIL if gas < GAS_CRIT_BNB else WARN)
            report(st, f"BNB (газ): {gas:.4f}" +
                       ("" if gas >= GAS_MIN_BNB else f"  мало для недели свопов (мин ~{GAS_MIN_BNB})"))
        else:
            report(WARN, "BNB не найден в balance — проверь газ вручную")
        if usdt is None and bnb is None:
            print("    raw balance →", bal)
    except TwakError as e:
        report(WARN, f"balance: {e}")

    # статус регистрации на хакатон
    try:
        cs = await twak.compete_status()
        registered = bool(cs.get("registered") or cs.get("isRegistered") or cs.get("status"))
        report(OK if registered else WARN, f"compete status: {cs}")
    except TwakError as e:
        report(WARN, f"compete status: {e} (регистрация уже подтверждена on-chain tx 0xd75091…)")


def check_journal() -> None:
    section("4. Journal / позиции")
    journal = TradeJournal()
    daily = DailyTradeMonitor(journal)
    trades = daily.trades_today()
    in_win = in_live_window()
    if in_win:
        report(OK if trades >= C.DAILY_MIN_TRADES else WARN,
               f"сделок сегодня (UTC): {trades} (нужно ≥{C.DAILY_MIN_TRADES} в окне; иначе сработает fallback)")
    else:
        report(OK, f"сделок сегодня (UTC): {trades} (вне окна — требование не активно)")

    try:
        positions = PositionRegistry(C.POSITIONS_FILE)
        open_pos = positions.open_positions()
        report(OK, f"открытых позиций: {len(open_pos)}")
        for p in open_pos:
            print(f"    · {p.symbol} {getattr(p,'tf','')} entry={getattr(p,'entry','?')} "
                  f"stop={getattr(p,'stop','?')} ride={getattr(p,'ride_mode',False)}")
    except Exception as e:
        report(WARN, f"не удалось прочитать позиции: {e}")

    tail = journal.recent(limit=8)
    if tail:
        print("    последние события:")
        for ev in tail:
            ts = ev.get("ts") or ev.get("time") or ""
            print(f"    · {ts} {ev.get('event','?')} {ev.get('symbol','') or ''}")


async def main() -> int:
    print("=" * 56)
    print(" TradingPalantir · PRE-FLIGHT (read-only)")
    print("=" * 56)
    check_config()
    check_service()
    await check_twak()
    check_journal()

    section("Вердикт")
    fails = [l for s, l in _results if s == FAIL]
    warns = [l for s, l in _results if s == WARN]
    if fails:
        print(f"{FAIL}  НЕ ГОТОВ — {len(fails)} блокер(ов):")
        for l in fails:
            print(f"        · {l}")
        rc = 2
    elif warns:
        print(f"{WARN}  ГОТОВ С ОГОВОРКАМИ — {len(warns)} предупреждение(й) (проверь выше)")
        rc = 1
    else:
        print(f"{OK}  ГОТОВ к live-флипу")
        rc = 0
    print("\nФлип в live:  config/settings.py → DRY_RUN=False  →  "
          "systemctl restart tradingpalantir  →  снова этот скрипт (ждём DRY_RUN=False + всё OK)")
    return rc


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
