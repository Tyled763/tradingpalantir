# =========================
# dashboard/app.py — TradingPalantir Command Center (Streamlit)
# Palantir-style read-only ops console. Source: journal/journal.db + positions.json.
# Запуск из tradingpalantir/:  streamlit run dashboard/app.py
# =========================
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import config as C

st.set_page_config(page_title="TradingPalantir", layout="wide", page_icon="🔮",
                   initial_sidebar_state="collapsed")

DB = C.JOURNAL_DB
POS = C.POSITIONS_FILE
REFRESH_SEC = 20

# ── палитра ───────────────────────────────────────────────
CYAN, VIOLET = "#22d3ee", "#818cf8"
GREEN, RED, AMBER, SLATE = "#34d399", "#f87171", "#fbbf24", "#64748b"
PANEL, PANEL2, LINE = "#121826", "#0f1420", "#1f2937"

REGIME_COLOR = {"risk_on": GREEN, "neutral": AMBER, "risk_off": RED}
GUARD_COLOR = {"normal": GREEN, "defensive": AMBER,
               "block_new_trades": RED, "emergency_flatten": RED}
EVENT_COLOR = {
    "ORDER_FILLED": GREEN, "RISK_APPROVED": GREEN, "RIDE_MODE_ON": CYAN,
    "TRADE_SIGNAL_CREATED": CYAN, "WATCHLIST_UPDATED": SLATE, "SCORE_UPDATED": SLATE,
    "ARMED_SET_UPDATED": VIOLET, "REGIME_UPDATED": VIOLET, "LLM_ANALYSIS_CREATED": VIOLET,
    "RISK_REJECTED": RED, "TRADE_REJECTED": RED, "TIGHT_STOP_SKIP": AMBER,
    "FIREWALL_REJECTED": RED, "DRAWDOWN_GUARD_TRIGGERED": RED, "FULL_CLOSE": AMBER,
    "STOP_MOVED": CYAN, "FALLBACK_TRADE": AMBER,
}

CSS = f"""
<style>
  .block-container {{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px; }}
  #MainMenu, footer, header {{ visibility: hidden; }}
  .tp-mono {{ font-family: 'SF Mono','JetBrains Mono',monospace; }}
  .tp-hero {{
    display:flex; align-items:center; justify-content:space-between;
    padding:18px 22px; border-radius:16px; margin-bottom:14px;
    background:linear-gradient(120deg, rgba(34,211,238,.10), rgba(129,140,248,.08));
    border:1px solid {LINE};
  }}
  .tp-title {{ font-size:1.55rem; font-weight:800; letter-spacing:.3px; margin:0;
    background:linear-gradient(90deg,{CYAN},{VIOLET}); -webkit-background-clip:text;
    -webkit-text-fill-color:transparent; }}
  .tp-sub {{ color:{SLATE}; font-size:.8rem; margin-top:2px; }}
  .tp-chip {{ display:inline-block; padding:4px 10px; margin:3px 4px 0 0; border-radius:999px;
    font-size:.72rem; font-family:'SF Mono',monospace; border:1px solid {LINE};
    background:{PANEL2}; color:{CYAN}; text-decoration:none; }}
  .tp-chip a {{ color:{CYAN}; text-decoration:none; }}
  .tp-card {{ background:{PANEL}; border:1px solid {LINE}; border-radius:14px;
    padding:14px 16px; height:100%; }}
  .tp-klabel {{ color:{SLATE}; font-size:.72rem; text-transform:uppercase; letter-spacing:.6px; }}
  .tp-kval {{ font-size:1.5rem; font-weight:750; margin-top:3px; }}
  .tp-ksub {{ font-size:.74rem; color:{SLATE}; margin-top:2px; font-family:'SF Mono',monospace; }}
  .tp-funnel {{ display:flex; align-items:stretch; gap:0; margin:6px 0 4px; flex-wrap:wrap; }}
  .tp-stage {{ flex:1; min-width:120px; text-align:center; background:{PANEL}; border:1px solid {LINE};
    border-radius:12px; padding:12px 8px; margin:0 5px; position:relative; }}
  .tp-stage .n {{ font-size:1.5rem; font-weight:800; }}
  .tp-stage .l {{ font-size:.7rem; color:{SLATE}; text-transform:uppercase; letter-spacing:.5px; margin-top:2px; }}
  .tp-arrow {{ display:flex; align-items:center; color:{SLATE}; font-size:1.2rem; }}
  .tp-badge {{ padding:2px 9px; border-radius:6px; font-size:.72rem; font-weight:700;
    font-family:'SF Mono',monospace; }}
  .tp-sec {{ font-size:.78rem; font-weight:700; color:{SLATE}; text-transform:uppercase;
    letter-spacing:1px; margin:14px 0 8px; }}
  .tp-llm {{ background:{PANEL}; border:1px solid {LINE}; border-left:3px solid {VIOLET};
    border-radius:10px; padding:10px 12px; margin-bottom:8px; }}
  .tp-llm .h {{ font-size:.82rem; font-weight:700; margin-bottom:3px; }}
  .tp-llm .b {{ font-size:.76rem; color:#aab4c4; line-height:1.35; }}
  div[data-testid="stDataFrame"] {{ border:1px solid {LINE}; border-radius:12px; }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


@st.cache_data(ttl=12)
def q(sql: str, args: tuple = ()) -> pd.DataFrame:
    if not os.path.exists(DB):
        return pd.DataFrame()
    con = sqlite3.connect(DB)
    try:
        return pd.read_sql_query(sql, con, params=args)
    finally:
        con.close()


def last_event(event: str) -> dict:
    df = q("SELECT ts,payload FROM events WHERE event=? ORDER BY ts DESC LIMIT 1", (event,))
    if df.empty:
        return {}
    d = json.loads(df.iloc[0]["payload"] or "{}")
    d["_ts"] = df.iloc[0]["ts"]
    return d


def count_since(event: str, ts0: float) -> int:
    df = q("SELECT COUNT(*) n FROM events WHERE event=? AND ts>=?", (event, ts0))
    return int(df.iloc[0]["n"]) if not df.empty else 0


def fmt_ts(ts) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")


def kpi(col, label, value, sub="", color="#dbe2ee"):
    col.markdown(
        f'<div class="tp-card"><div class="tp-klabel">{label}</div>'
        f'<div class="tp-kval" style="color:{color}">{value}</div>'
        f'<div class="tp-ksub">{sub}</div></div>', unsafe_allow_html=True)


def badge(text, color):
    return f'<span class="tp-badge" style="background:{color}22;color:{color};border:1px solid {color}55">{text}</span>'


# ══════════════════════════════════════════════════════════
day0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
regime = last_event("REGIME_UPDATED")
armed_ev = last_event("ARMED_SET_UPDATED")
dd_ev = last_event("DRAWDOWN_GUARD_TRIGGERED")
armed = armed_ev.get("armed", [])
thr = armed_ev.get("threshold", getattr(C, "SCORE_ENTRY_THRESHOLD", 80))
in_live = C.LIVE_WINDOW[0] <= datetime.now(timezone.utc).date().isoformat() <= C.LIVE_WINDOW[1]

# позиции / equity
positions = []
if os.path.exists(POS):
    blob = json.load(open(POS))
    plist = blob.get("positions", blob) if isinstance(blob, dict) else blob
    positions = list(plist.values()) if isinstance(plist, dict) else plist
closed = [p for p in positions if p.get("realized_pnl") is not None]
open_pos = [p for p in positions if p.get("state") == "open"]
realized = sum(p.get("realized_pnl") or 0 for p in closed)
equity = C.PAPER_EQUITY + realized
n_fills = count_since("ORDER_FILLED", day0)
n_setups = count_since("TRADE_SIGNAL_CREATED", day0)
n_skips = count_since("TIGHT_STOP_SKIP", day0)

# ── HERO ──────────────────────────────────────────────────
mode_chip = badge("● PAPER", AMBER) if C.DRY_RUN else badge("● LIVE", GREEN)
st.markdown(f"""
<div class="tp-hero">
  <div>
    <div class="tp-title">🔮 TradingPalantir — Command Center</div>
    <div class="tp-sub">Autonomous spot-trading agent · BNB Smart Chain · CoinMarketCap × Trust Wallet</div>
  </div>
  <div style="text-align:right">
    {mode_chip}
    <a class="tp-chip" href="https://bscscan.com/address/0xAaD844634247B124Eb8cA93378fF7E3608E7a290" target="_blank">wallet 0xAaD8…a290</a>
    <a class="tp-chip" href="https://bscscan.com/tx/0xb434847f03f449df059e13ad09447dc3b3ca6765dbc3ca551a9217bc90e180a7" target="_blank">ERC-8004 #132867</a>
    <a class="tp-chip" href="https://bscscan.com/tx/0xd75091adb91e58ac97523311057b96254b752ef6ef9abddfb4649b52d403780e" target="_blank">registered ✓</a>
  </div>
</div>
""", unsafe_allow_html=True)

# ── KPI ───────────────────────────────────────────────────
k = st.columns(6)
rc = REGIME_COLOR.get(regime.get("global_regime"), SLATE)
kpi(k[0], "Market regime", regime.get("global_regime", "—").replace("_", " "),
    regime.get("market_state", ""), rc)
gm = dd_ev.get("mode", "normal")
kpi(k[1], "Risk / DD guard", gm.replace("_", " "),
    f"{dd_ev.get('dd_pct', 0)}% drawdown", GUARD_COLOR.get(gm, GREEN))
kpi(k[2], "Armed set", len(armed),
    " ".join(a[0] for a in armed[:3]) if armed else f"waiting ≥{thr}", CYAN)
pnl_c = GREEN if realized >= 0 else RED
kpi(k[3], "Equity (paper)", f"${equity:,.2f}",
    f"{realized:+.3f} realized · {len(closed)} closed", pnl_c)
kpi(k[4], "Open positions", len(open_pos),
    f"{n_fills} fills today", VIOLET if open_pos else SLATE)
kpi(k[5], "Setups today", n_setups,
    f"{n_skips} tight-stop skips", CYAN)

# ── FUNNEL ────────────────────────────────────────────────
wl = last_event("WATCHLIST_UPDATED").get("watchlist", [])
stages = [("128", "scored"), (str(len(wl)), "watchlist"),
          (str(len(armed)), f"armed ≥{thr}"), (str(n_setups), "setups today"),
          (str(n_fills), "fills"), (str(len(open_pos)), "open")]
cells = []
for i, (n, l) in enumerate(stages):
    cells.append(f'<div class="tp-stage"><div class="n" style="color:{CYAN if i<3 else GREEN}">{n}</div>'
                 f'<div class="l">{l}</div></div>')
    if i < len(stages) - 1:
        cells.append('<div class="tp-arrow">→</div>')
st.markdown('<div class="tp-funnel">' + "".join(cells) + '</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)
left, right = st.columns([3, 2], gap="medium")

# ── WATCHLIST ─────────────────────────────────────────────
with left:
    st.markdown('<div class="tp-sec">Stage A → B · Opportunity Radar (score 1–100)</div>',
                unsafe_allow_html=True)
    scores = q("SELECT symbol, payload, MAX(ts) ts FROM events "
               "WHERE event='SCORE_UPDATED' GROUP BY symbol")
    comp = {r["symbol"]: json.loads(r["payload"] or "{}") for _, r in scores.iterrows()}
    if wl:
        rows = []
        for sym, score in wl:
            p = comp.get(sym, {})
            c = p.get("components", {})
            rows.append({"": "🟢" if score >= thr else "", "symbol": sym,
                         "score": float(score),
                         "fw": "✓" if p.get("firewall") == "approved" else
                               ("✕" if p.get("firewall") == "rejected" else "·"),
                         "liq": c.get("liquidity"), "mom": c.get("momentum"),
                         "brk": c.get("breakout"), "trd": c.get("trend"),
                         "soc": c.get("social")})
        df = pd.DataFrame(rows)
        st.dataframe(
            df, hide_index=True, height=430, use_container_width=True,
            column_config={
                "": st.column_config.TextColumn("", width="small"),
                "symbol": st.column_config.TextColumn("Token", width="small"),
                "score": st.column_config.ProgressColumn(
                    "Score", min_value=0, max_value=100, format="%.1f"),
                "fw": st.column_config.TextColumn("FW", width="small", help="firewall"),
                "liq": st.column_config.NumberColumn("Liq", format="%.0f", width="small"),
                "mom": st.column_config.NumberColumn("Mom", format="%.0f", width="small"),
                "brk": st.column_config.NumberColumn("Brk", format="%.0f", width="small"),
                "trd": st.column_config.NumberColumn("Trd", format="%.0f", width="small"),
                "soc": st.column_config.NumberColumn("Soc", format="%.0f", width="small"),
            })
        st.caption("🟢 armed → монета мониторится по 4 ТФ на FVG/VWAP/EMA-вход. "
                   "Компоненты: ликвидность · momentum · breakout · trend · social.")
    else:
        st.info("Скоринг ещё не записан — ждём первый rescreen.")

# ── POSITIONS + LLM ───────────────────────────────────────
with right:
    st.markdown('<div class="tp-sec">Positions</div>', unsafe_allow_html=True)
    if positions:
        df = pd.DataFrame(positions)
        for col in ("entry", "stop", "tp", "qty", "realized_pnl"):
            if col not in df:
                df[col] = None
        ride_col = df["ride_mode"] if "ride_mode" in df else pd.Series([False] * len(df))
        df["ride"] = ride_col.map(lambda x: "🏄" if x else "")
        show = df[["sid", "symbol", "state", "tf", "entry", "stop",
                   "realized_pnl", "ride"]].sort_values("sid", ascending=False)

        def _pnl_color(v):
            if v is None or (isinstance(v, float) and v != v):
                return ""
            return f"color:{GREEN}" if v >= 0 else f"color:{RED}"
        sty = show.style.map(_pnl_color, subset=["realized_pnl"]).format(
            {"entry": "{:.5g}", "stop": "{:.5g}", "realized_pnl": "{:+.3f}"}, na_rep="—")
        st.dataframe(sty, hide_index=True, height=200, use_container_width=True,
                     column_config={"sid": st.column_config.NumberColumn("#", width="small"),
                                    "realized_pnl": st.column_config.TextColumn("PnL")})
    else:
        st.info("Позиций ещё нет.")

    st.markdown('<div class="tp-sec">Claude decisions (analyst → reviewer)</div>',
                unsafe_allow_html=True)
    llm = q("SELECT ts, symbol, payload FROM events WHERE event='LLM_ANALYSIS_CREATED' "
            "ORDER BY ts DESC LIMIT 4")
    if llm.empty:
        st.caption("Решений ещё не было.")
    for _, r in llm.iterrows():
        p = json.loads(r["payload"] or "{}")
        enter = p.get("enter")
        vb = badge("ENTER", GREEN) if enter else badge("VETO", RED)
        rat = str(p.get("rationale") or "")[:240]
        st.markdown(f'<div class="tp-llm"><div class="h">{vb} &nbsp;{r["symbol"]} '
                    f'<span style="color:{SLATE};font-weight:400">· sf={p.get("size_factor")} '
                    f'· {fmt_ts(r["ts"])} UTC</span></div>'
                    f'<div class="b">{rat}…</div></div>', unsafe_allow_html=True)

# ── EVENT STREAM ──────────────────────────────────────────
st.markdown('<div class="tp-sec">Event stream</div>', unsafe_allow_html=True)
ev = q("SELECT ts, event, symbol, payload FROM events ORDER BY ts DESC LIMIT 40")
if not ev.empty:
    ev["time"] = ev["ts"].map(fmt_ts)
    ev["payload"] = ev["payload"].str.slice(0, 120)
    disp = ev[["time", "event", "symbol", "payload"]].rename(
        columns={"time": "UTC", "event": "Event", "symbol": "Token", "payload": "Detail"})

    def _ev_color(row):
        c = EVENT_COLOR.get(row["Event"], "#dbe2ee")
        return [f"color:{c}" if col == "Event" else "" for col in row.index]
    st.dataframe(disp.style.apply(_ev_color, axis=1), hide_index=True,
                 height=360, use_container_width=True)
else:
    st.info("Журнал пуст.")

st.caption(f"Auto-refresh {REFRESH_SEC}s · journal {DB} · "
           f"updated {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")

components.html(
    f"<script>setTimeout(()=>window.parent.location.reload(), {REFRESH_SEC*1000});</script>",
    height=0)
