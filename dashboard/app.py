# =========================
# dashboard/app.py — TradingPalantir Command Center (Streamlit, read-only)
# Запуск из tradingpalantir/:  streamlit run dashboard/app.py
# Понятность системы за 2 минуты (§21): режим → воронка → позиции → риск →
# решения LLM → события. Источники: journal/journal.db + positions.json.
# =========================
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st

import config as C

st.set_page_config(page_title="TradingPalantir", layout="wide", page_icon="🔮")

DB = C.JOURNAL_DB
POS = C.POSITIONS_FILE


@st.cache_data(ttl=15)
def q(sql: str, args: tuple = ()) -> pd.DataFrame:
    if not os.path.exists(DB):
        return pd.DataFrame()
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, con, params=args)
    finally:
        con.close()


def last_event(event: str) -> dict:
    df = q("SELECT ts,payload FROM events WHERE event=? ORDER BY ts DESC LIMIT 1",
           (event,))
    if df.empty:
        return {}
    d = json.loads(df.iloc[0]["payload"] or "{}")
    d["_ts"] = df.iloc[0]["ts"]
    return d


def fmt_ts(ts) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M UTC")


st.title("🔮 TradingPalantir — Command Center")
st.caption(f"agent `0xAaD8…a290` · ERC-8004 #132867 · paper={C.DRY_RUN} · "
           f"score threshold {C.SCORE_ENTRY_THRESHOLD}")

# ── строка 1: режим / guard / воронка / сделки сегодня ────
regime = last_event("REGIME_UPDATED")
armed_ev = last_event("ARMED_SET_UPDATED")
dd_ev = last_event("DRAWDOWN_GUARD_TRIGGERED")
day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp()
fills_today = q("SELECT COUNT(*) n FROM events WHERE event='ORDER_FILLED' AND ts>=?",
                (day0,))
n_fills = int(fills_today.iloc[0]["n"]) if not fills_today.empty else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Market regime", regime.get("global_regime", "—"),
          regime.get("market_state", ""))
c2.metric("Risk budget", regime.get("risk_budget", "—"))
c3.metric("DD guard", dd_ev.get("mode", "normal"),
          f"{dd_ev.get('dd_pct', 0)}% dd" if dd_ev else "")
armed = armed_ev.get("armed", [])
c4.metric("Armed set", len(armed),
          ", ".join(a[0] for a in armed[:4]) if armed else "ждём 90+")
c5.metric("Fills today", n_fills,
          "live window" if C.LIVE_WINDOW[0] <= datetime.now(timezone.utc).date().isoformat() <= C.LIVE_WINDOW[1] else "pre-window")

st.divider()
left, right = st.columns([3, 2])

# ── watchlist со скорами ──────────────────────────────────
with left:
    st.subheader("Stage A → B: Watchlist (топ-20, score 1..100)")
    wl = last_event("WATCHLIST_UPDATED").get("watchlist", [])
    scores = q("SELECT symbol, payload, MAX(ts) ts FROM events "
               "WHERE event='SCORE_UPDATED' GROUP BY symbol")
    comp_by_sym = {}
    for _, r in scores.iterrows():
        p = json.loads(r["payload"] or "{}")
        comp_by_sym[r["symbol"]] = p
    if wl:
        rows = []
        for sym, score in wl:
            p = comp_by_sym.get(sym, {})
            c = p.get("components", {})
            rows.append({"symbol": sym, "score": score,
                         "armed": "🟢" if score >= C.SCORE_ENTRY_THRESHOLD else "",
                         "firewall": p.get("firewall", "?"),
                         **{k: c.get(k) for k in
                            ("liquidity", "momentum", "social", "perp", "regime")}})
        st.dataframe(pd.DataFrame(rows), height=420, use_container_width=True)
    else:
        st.info("Скоринг ещё не записан — агент должен пройти первый rescreen.")

# ── позиции ───────────────────────────────────────────────
with right:
    st.subheader("Positions")
    if os.path.exists(POS):
        blob = json.load(open(POS))
        plist = blob.get("positions", blob) if isinstance(blob, dict) else blob
        if isinstance(plist, dict):
            plist = list(plist.values())
        df = pd.DataFrame(plist)
        if not df.empty:
            cols = [c for c in ("sid", "symbol", "state", "tf", "setup", "entry",
                                "stop", "tp", "qty", "ride_mode", "realized_pnl")
                    if c in df.columns]
            st.dataframe(df[cols].sort_values("sid", ascending=False),
                         height=200, use_container_width=True)
            closed = df[df.get("realized_pnl").notna()] if "realized_pnl" in df else pd.DataFrame()
            if not closed.empty:
                st.metric("Realized PnL (paper)", f"{closed['realized_pnl'].sum():+.3f} USDT",
                          f"{len(closed)} closed")
        else:
            st.info("Позиций ещё нет.")
    else:
        st.info("Позиций ещё нет.")

    st.subheader("LLM decisions")
    llm = q("SELECT ts, symbol, payload FROM events "
            "WHERE event IN ('LLM_ANALYSIS_CREATED','LLM_REVIEW_CREATED') "
            "ORDER BY ts DESC LIMIT 5")
    for _, r in llm.iterrows():
        p = json.loads(r["payload"] or "{}")
        st.caption(f"**{r['symbol']}** {fmt_ts(r['ts'])} · enter={p.get('enter')} "
                   f"sf={p.get('size_factor')}\n\n{str(p.get('rationale'))[:220]}…")
    if llm.empty:
        st.caption("Решений LLM ещё не было (сигналов не поступало).")

st.divider()
st.subheader("Event stream (последние 30)")
ev = q("SELECT ts, event, symbol, payload FROM events ORDER BY ts DESC LIMIT 30")
if not ev.empty:
    ev["time"] = ev["ts"].map(fmt_ts)
    ev["payload"] = ev["payload"].str.slice(0, 110)
    st.dataframe(ev[["time", "event", "symbol", "payload"]],
                 height=420, use_container_width=True)
else:
    st.info("Журнал пуст.")
