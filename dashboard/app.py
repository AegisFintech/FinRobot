import json
import math
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state" / "moonshot"
LOG_DIR = ROOT / "logs"
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "15"))
AUTO_REFRESH = os.getenv("DASHBOARD_AUTO_REFRESH", "false").lower() in {"1", "true", "yes", "on"}
MYFXBOOK_URL = "https://www.myfxbook.com/members/AloysiusChan/trending/11809640"

st.set_page_config(page_title="FinRobot Command Center", page_icon="📈", layout="wide")
st.markdown(
    """
    <style>
      .block-container { padding-top: 1.25rem; padding-bottom: 2rem; }
      [data-testid="stMetricValue"] { font-size: 1.7rem; }
      .small-muted { color: #888; font-size: 0.85rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def read_json(path: Path, default):
    try:
        if not path.exists() or path.stat().st_size == 0:
            return default
        return json.loads(path.read_text())
    except Exception:
        return default


def read_jsonl(path: Path) -> pd.DataFrame:
    rows = []
    try:
        if path.exists():
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return pd.DataFrame(rows)


def fmt_money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def fmt_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "—"


def ts_to_dt(series):
    return pd.to_datetime(series, unit="s", errors="coerce")


def summarize_trades(df: pd.DataFrame):
    if df.empty or "pnl" not in df:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}
    d = df.copy()
    d["pnl"] = pd.to_numeric(d["pnl"], errors="coerce").fillna(0.0)
    wins = d[d["pnl"] > 0]
    losses = d[d["pnl"] <= 0]
    gross_win = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    return {
        "trades": len(d),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(d) * 100 if len(d) else 0,
        "total_pnl": d["pnl"].sum(),
        "avg_win": wins["pnl"].mean() if len(wins) else 0,
        "avg_loss": losses["pnl"].mean() if len(losses) else 0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else math.inf,
        "expectancy": d["pnl"].mean() if len(d) else 0,
    }


def recent_log(path: Path, lines=160):
    try:
        if not path.exists():
            return ""
        data = path.read_text(errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except Exception as exc:
        return f"Could not read log: {exc}"


def find_mt5_status_path() -> Path | None:
    candidates = [
        Path("/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_status.json"),
        Path("/home/openclaw/mt5/terminal/current/MQL5/Files/finrobot_status.json"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def read_mt5_status():
    path = find_mt5_status_path()
    if not path:
        return {}, None, None
    status = read_json(path, {})
    ts = status.get("ts")
    age = time.time() - float(ts or 0) if ts else None
    return status, path, age


def bool_badge(value):
    return "🟢 Enabled" if bool(value) else "🔴 Disabled"


def shell_lines(cmd: list[str], limit=80):
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=6)
        return "\n".join(out.splitlines()[-limit:])
    except Exception as exc:
        return f"Unavailable: {exc}"


def recent_utf16_log(path: Path | None, lines=120):
    try:
        if not path or not path.exists():
            return ""
        text = path.read_bytes().decode("utf-16", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception as exc:
        return f"Could not read MT5 log: {exc}"


def extract_decision_lines(text: str, source: str, limit: int = 80) -> pd.DataFrame:
    keywords = [
        "signal", "decision", "reject", "rejected", "accepted", "filled", "order",
        "max positions", "funding rates", "rolling backtest", "summary", "promoted",
        "rollback", "pause", "blacklist", "disabled", "enabled", "auto_", "no_signal",
    ]
    rows = []
    for line in (text or "").splitlines():
        low = line.lower()
        if not any(k in low for k in keywords):
            continue
        ts = ""
        event = line
        m = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?)\s*(?:\|\s*)?(.*)$", line)
        if m:
            ts, event = m.group(1), m.group(2)
        rows.append({"source": source, "time": ts, "decision / signal": event[-500:]})
    return pd.DataFrame(rows[-limit:])


def mt5_common_path(name: str) -> Path | None:
    candidates = [
        Path("/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files") / name,
        Path("/home/openclaw/.wine-mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files") / name,
        Path("/home/openclaw/mt5/terminal/current/MQL5/Files") / name,
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def mt5_ack_table(limit: int = 80) -> pd.DataFrame:
    path = mt5_common_path("finrobot_acks.csv")
    if path and path.stat().st_size:
        rows = []
        for line in path.read_text(errors="replace").splitlines():
            if not line.strip() or line.lower().startswith("id"):
                continue
            parts = re.split(r"[\t,]", line)
            rows.append({
                "id": parts[0] if len(parts) > 0 else "",
                "time": parts[1] if len(parts) > 1 else "",
                "status": parts[2] if len(parts) > 2 else "",
                "message": parts[3] if len(parts) > 3 else "",
                "symbol": parts[4] if len(parts) > 4 else "",
                "side": parts[5] if len(parts) > 5 else "",
                "volume": parts[6] if len(parts) > 6 else "",
                "price": parts[7] if len(parts) > 7 else "",
            })
        return pd.DataFrame(rows[-limit:])
    return pd.DataFrame()


def mt5_positions_table() -> pd.DataFrame:
    path = mt5_common_path("finrobot_positions.csv")
    if path and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def mt5_deals_table() -> pd.DataFrame:
    path = mt5_common_path("finrobot_deals.csv")
    if path and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def latest_signal_snapshot(mt5_status: dict, mt5_age: float | None, positions: dict, overrides: dict) -> pd.DataFrame:
    symbols = mt5_status.get("symbols") if isinstance(mt5_status, dict) else None
    rows = []
    if isinstance(symbols, list) and symbols:
        for item in symbols:
            rows.append({
                "system": "MT5",
                "decision": item.get("last_signal", "—"),
                "instrument": item.get("symbol", "—"),
                "price / spread": f"bid {item.get('bid', '—')} / ask {item.get('ask', '—')} / spread {item.get('spread_points', '—')} pts",
                "positions": f"{item.get('auto_positions', 0)} managed",
                "status": f"heartbeat {mt5_age:.0f}s" if mt5_age is not None else "no heartbeat",
            })
    else:
        rows.append({
            "system": "MT5",
            "decision": mt5_status.get("last_auto_signal", "—"),
            "instrument": mt5_status.get("symbol", "XAUUSD / BTCUSD"),
            "price / spread": "waiting for v1.20 heartbeat",
            "positions": str(mt5_status.get("positions", 0)),
            "status": f"heartbeat {mt5_age:.0f}s" if mt5_age is not None else "no heartbeat",
        })
    return pd.DataFrame(rows)


def latest_mt5_journal():
    roots = [
        Path("/home/openclaw/mt5/terminal/current/logs"),
        Path("/home/openclaw/.wine-mt5/drive_c/ICMarketsSCOfficialMT5/logs"),
        Path("/home/openclaw/.wine-mt5/drive_c/FinRobotMT5/logs"),
    ]
    logs = []
    for root in roots:
        if root.exists():
            logs.extend(root.glob("*.log"))
    return max(logs, key=lambda x: x.stat().st_mtime) if logs else None


overrides = read_json(STATE_DIR / "runtime_overrides.json", {})
journal = read_jsonl(STATE_DIR / "improver_journal.jsonl")
mt5_status, mt5_status_path, mt5_age = read_mt5_status()
mt5_deals = mt5_deals_table()
mt5_positions = mt5_positions_table()
mt5_live = mt5_age is not None and mt5_age < 30


def summarize_mt5_deals(df: pd.DataFrame):
    if df.empty or "profit" not in df:
        return {"deals": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}
    d = df.copy()
    d["profit"] = pd.to_numeric(d["profit"], errors="coerce").fillna(0.0)
    wins = d[d["profit"] > 0]
    losses = d[d["profit"] <= 0]
    gross_win = wins["profit"].sum()
    gross_loss = abs(losses["profit"].sum())
    return {
        "deals": len(d),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(d) * 100 if len(d) else 0,
        "total_pnl": d["profit"].sum(),
        "avg_win": wins["profit"].mean() if len(wins) else 0,
        "avg_loss": losses["profit"].mean() if len(losses) else 0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else math.inf,
        "expectancy": d["profit"].mean() if len(d) else 0,
    }


mt5_stats = summarize_mt5_deals(mt5_deals)

st.title("📈 FinRobot MT5 Command Center")
st.caption(
    f"{'🟢' if mt5_live else '🟠'} MT5 {'LIVE' if mt5_live else 'STALE'} · "
    f"Manual refresh · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)

with st.sidebar:
    st.markdown("### Refresh")
    st.caption("Auto-refresh is off, so the page will not jump while you read.")
    if st.button("Refresh now", width="stretch"):
        st.rerun()
    if AUTO_REFRESH:
        st.warning(f"Full-page auto-refresh is enabled by env every {REFRESH_SECONDS}s.")
        st.markdown(f"<meta http-equiv='refresh' content='{REFRESH_SECONDS}'>", unsafe_allow_html=True)

command_tab, signals_tab, mt5_tab, logs_tab = st.tabs(["Command Center", "Signal Decisions", "MT5 XAUUSD/BTCUSD", "Read-only Logs"])

with command_tab:
    st.subheader("Overview")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("MT5 Balance", fmt_money(mt5_status.get("balance")))
    c2.metric("MT5 Equity", fmt_money(mt5_status.get("equity")))
    c3.metric("Open Positions", int(mt5_status.get("positions") or 0))
    c4.metric("Closed Deals", int(mt5_stats.get("deals", 0)))
    c5.metric("Win Rate", f"{mt5_stats.get('win_rate', 0):.1f}%")
    pf = mt5_stats.get("profit_factor", 0)
    c6.metric("Profit Factor", "∞" if pf == math.inf else f"{pf:.2f}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Closed PnL", fmt_money(mt5_stats.get("total_pnl", 0)))
    m2.metric("Free Margin", fmt_money(mt5_status.get("free_margin")))
    m3.metric("EA Heartbeat", f"{mt5_age:.0f}s" if mt5_age is not None else "—")
    m4.metric("MT5 Login", str(mt5_status.get("login") or "—"))

    if mt5_age is not None:
        st.markdown(f"<div class='small-muted'>MT5 heartbeat: {mt5_age:.0f}s ago · {mt5_status_path}</div>", unsafe_allow_html=True)

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Open managed MT5 positions")
        if not mt5_positions.empty:
            st.dataframe(mt5_positions, width="stretch", hide_index=True)
        else:
            st.info("No managed MT5 positions exported yet.")

        st.subheader("Closed MT5 PnL")
        if not mt5_deals.empty and "profit" in mt5_deals:
            chart_df = mt5_deals.copy()
            chart_df["profit"] = pd.to_numeric(chart_df["profit"], errors="coerce").fillna(0)
            if "time" in chart_df:
                chart_df["time"] = pd.to_datetime(chart_df["time"], errors="coerce")
                chart_df = chart_df.sort_values("time")
            chart_df["cum_pnl"] = chart_df["profit"].cumsum()
            fig = go.Figure()
            if "time" in chart_df:
                fig.add_trace(go.Scatter(x=chart_df["time"], y=chart_df["cum_pnl"], mode="lines+markers", name="Cumulative PnL"))
            else:
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["cum_pnl"], mode="lines+markers", name="Cumulative PnL"))
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), yaxis_title="Account currency")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No closed MT5 deal data yet.")

    with right:
        st.subheader("Auto-Improver")
        values = overrides.get("values", overrides if isinstance(overrides, dict) else {})
        updated_at = overrides.get("updated_at") if isinstance(overrides, dict) else None
        if values:
            st.json({"updated": datetime.fromtimestamp(updated_at).isoformat() if updated_at else None, "values": values})
        else:
            st.info("No runtime overrides applied.")

        if not journal.empty:
            st.subheader("Improver Decisions")
            j = journal.tail(8).copy()
            cols = [c for c in ["ts", "event", "applied", "decision", "reason", "error", "skipped"] if c in j.columns]
            if "ts" in j:
                j["time"] = ts_to_dt(j["ts"])
                cols = ["time"] + [c for c in cols if c != "ts"]
            st.dataframe(j[cols].astype(str), width="stretch", hide_index=True)

    st.markdown("### External verified performance")
    st.link_button("Open Myfxbook: Trending", MYFXBOOK_URL)
    st.caption("Myfxbook is linked instead of controlled from this dashboard. If iframe embedding is blocked, the button remains reliable.")

with signals_tab:
    st.subheader("Signal Decisions — read-only live feed")
    st.caption("This collects MT5 XAUUSD/BTCUSD decisions, acknowledgements, journal lines, and autonomous-review outcomes.")

    st.markdown("### Current decision snapshot")
    st.dataframe(latest_signal_snapshot(mt5_status, mt5_age, {}, overrides), width="stretch", hide_index=True)

    st.markdown("### MT5 EA decisions")
    g1, g2, g3, g4, g5 = st.columns(5)
    g1.metric("Last EA signal", str(mt5_status.get("last_auto_signal", "—")))
    g2.metric("MT5 positions", int(mt5_status.get("positions") or 0))
    g3.metric("Spread", f"{mt5_status.get('spread_points', '—')} pts")
    g4.metric("Bid", str(mt5_status.get("bid", "—")))
    g5.metric("Ask", str(mt5_status.get("ask", "—")))

    ack_df = mt5_ack_table()
    if not ack_df.empty:
        st.markdown("#### MT5 EA acknowledgements / trade decisions")
        st.dataframe(ack_df, width="stretch", hide_index=True)
    else:
        st.info("No MT5 acknowledgement rows yet. The live EA signal is shown above from the heartbeat.")

    st.markdown("### Autonomous review decisions")
    review_decisions = extract_decision_lines(recent_log(LOG_DIR / "autonomous_review.log", 500), "autonomous-review", 120)
    improver_decisions = extract_decision_lines(recent_log(LOG_DIR / "improver.log", 220), "strategy-improver", 60)
    combined = pd.concat([df for df in [review_decisions, improver_decisions] if not df.empty], ignore_index=True) if (not review_decisions.empty or not improver_decisions.empty) else pd.DataFrame()
    if not combined.empty:
        st.dataframe(combined.tail(160), width="stretch", hide_index=True)
    else:
        st.info("No autonomous review decision lines found yet.")

    st.markdown("### MT5 journal decision lines")
    journal_path = latest_mt5_journal()
    mt5_decisions = extract_decision_lines(recent_utf16_log(journal_path, 300), "mt5-journal", 100) if journal_path else pd.DataFrame()
    if not mt5_decisions.empty:
        st.dataframe(mt5_decisions, width="stretch", hide_index=True)
    else:
        st.info("No MT5 journal decision lines found yet.")


with mt5_tab:
    st.subheader("MT5 Demo — XAUUSD / BTCUSD")
    a, b, c, d, e, f = st.columns(6)
    a.metric("Balance", fmt_money(mt5_status.get("balance")))
    b.metric("Equity", fmt_money(mt5_status.get("equity")))
    c.metric("Margin", fmt_money(mt5_status.get("margin")))
    d.metric("Free Margin", fmt_money(mt5_status.get("free_margin")))
    e.metric("Positions", int(mt5_status.get("positions") or 0))
    f.metric("Heartbeat", f"{mt5_age:.0f}s" if mt5_age is not None else "—")

    st.markdown("#### Account / EA status")
    status_rows = [
        {"field": "Login", "value": mt5_status.get("login", "—")},
        {"field": "Server", "value": mt5_status.get("server", "—")},
        {"field": "Terminal trading", "value": bool_badge(mt5_status.get("trade_allowed_terminal"))},
        {"field": "EA trading", "value": bool_badge(mt5_status.get("trade_allowed_ea"))},
        {"field": "Last command id", "value": mt5_status.get("last_command_id", "—")},
        {"field": "Heartbeat file", "value": str(mt5_status_path or "not found")},
    ]
    st.dataframe(pd.DataFrame(status_rows).astype(str), width="stretch", hide_index=True)

    col_a, col_b = st.columns([1, 1])
    with col_a:
        st.markdown("#### Raw heartbeat")
        st.json(mt5_status or {"status": "not found"})
        pos_df = mt5_positions
        st.markdown("#### Open managed positions")
        if not pos_df.empty:
            st.dataframe(pos_df, width="stretch", hide_index=True)
        else:
            st.info("No managed MT5 positions exported yet.")
        deals_df = mt5_deals
        st.markdown("#### Recent managed deals")
        if not deals_df.empty:
            st.dataframe(deals_df.tail(80), width="stretch", hide_index=True)
        else:
            st.info("No managed MT5 deals exported yet.")
    with col_b:
        st.markdown("#### Myfxbook")
        st.write("Full verified trading analytics live on Myfxbook.")
        st.link_button("Open Myfxbook report", MYFXBOOK_URL)

    journal_path = latest_mt5_journal()
    with st.expander("Latest MT5 journal", expanded=False):
        st.code(recent_utf16_log(journal_path, 160) if journal_path else "No MT5 journal found.", language="text")

with logs_tab:
    st.subheader("Read-only terminal / process view")
    st.caption("No command input is exposed here — only status and logs.")
    p1, p2 = st.columns(2)
    with p1:
        st.markdown("#### PM2")
        st.code(shell_lines(["bash", "-lc", "runuser -l openclaw -c 'pm2 list'"], 80), language="text")
    with p2:
        st.markdown("#### MT5 processes")
        st.code(shell_lines(["bash", "-lc", "pgrep -af 'terminal64.exe|xvfb|wineserver|start_mt5' || true"], 80), language="text")

    log_choice = st.selectbox("FinRobot log", ["autonomous_review.log", "pm2_autonomous_review.out.log", "pm2_autonomous_review.err.log", "pm2_dashboard.err.log", "pm2_mt5.err.log", "health.log"])
    st.code(recent_log(LOG_DIR / log_choice, 220), language="text")

    journal_path = latest_mt5_journal()
    st.markdown("#### MT5 journal")
    st.code(recent_utf16_log(journal_path, 220) if journal_path else "No MT5 journal found.", language="text")
