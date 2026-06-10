# =========================
# journal/trade_journal.py — TradingPalantir
# Единый журнал решений/событий: JSONL (человекочитаемый) + SQLite (дашборд).
# Каждое событие: ts, event, symbol?, payload(JSON). Никогда не падает —
# журнал не должен ронять торговый цикл.
# =========================
from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, Optional

import config as C


class TradeJournal:
    def __init__(self, db_path: Optional[str] = None, jsonl_path: Optional[str] = None):
        self.jsonl = jsonl_path or C.JOURNAL_JSONL
        self.db_path = db_path or C.JOURNAL_DB
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._db = sqlite3.connect(self.db_path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL NOT NULL, event TEXT NOT NULL,"
            " symbol TEXT, payload TEXT)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts)")
        self._db.execute("CREATE INDEX IF NOT EXISTS ix_events_event ON events(event)")
        self._db.commit()

    def log(self, event: str, symbol: Optional[str] = None, **payload: Any) -> None:
        rec = {"ts": time.time(), "event": event, "symbol": symbol, **payload}
        try:
            with open(self.jsonl, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
        try:
            self._db.execute(
                "INSERT INTO events (ts, event, symbol, payload) VALUES (?,?,?,?)",
                (rec["ts"], event, symbol,
                 json.dumps(payload, ensure_ascii=False, default=str)),
            )
            self._db.commit()
        except sqlite3.Error:
            pass

    # ── выборки для дашборда/мониторов ────────────────────
    def count_since(self, event: str, since_ts: float) -> int:
        cur = self._db.execute(
            "SELECT COUNT(*) FROM events WHERE event=? AND ts>=?", (event, since_ts))
        return int(cur.fetchone()[0])

    def recent(self, limit: int = 50, event: Optional[str] = None) -> list:
        q = "SELECT ts,event,symbol,payload FROM events"
        args: tuple = ()
        if event:
            q += " WHERE event=?"; args = (event,)
        q += " ORDER BY ts DESC LIMIT ?"
        cur = self._db.execute(q, args + (limit,))
        return [{"ts": r[0], "event": r[1], "symbol": r[2],
                 "payload": json.loads(r[3] or "{}")} for r in cur.fetchall()]
