
import json
import hmac
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

# yfinance is intentionally imported only when prices are refreshed.
# This keeps initial app startup light.
DATA_FILE = Path("data/portfolio_data.json")
N_PORTFOLIOS = 10


# -----------------------------
# App configuration
# -----------------------------
st.set_page_config(
    page_title="Portfolio Hub",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .block-container {max-width: 1400px; padding-top: 1.4rem;}
    [data-testid="stMetric"] {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 12px;
        padding: 14px;
    }
    .small-muted {color: #8b93a7; font-size: 0.82rem;}
    .brand {font-size: 0.85rem; letter-spacing: .12em; color: #b7bdca;}
    .title {font-size: 2.1rem; font-weight: 750; margin-bottom: 0.2rem;}
    .card {
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 14px;
        padding: 18px;
        background: rgba(255,255,255,0.025);
    }
    div[data-testid="stButton"] > button {border-radius: 9px;}
</style>
""", unsafe_allow_html=True)


# -----------------------------
# Data layer
# -----------------------------
def default_data():
    portfolios = []
    for i in range(1, N_PORTFOLIOS + 1):
        portfolios.append({
            "id": i,
            "name": f"Portfolio {i:02d}",
            "description": "",
            "cash": 0.0,
            "holdings": [],
            "transactions": [],
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
    return {
        "version": 1,
        "last_saved": datetime.now().isoformat(timespec="seconds"),
        "portfolios": portfolios,
    }


def ensure_data_file():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text(json.dumps(default_data(), indent=2), encoding="utf-8")


def load_data():
    ensure_data_file()
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        backup = DATA_FILE.with_suffix(".broken.json")
        try:
            DATA_FILE.replace(backup)
        except Exception:
            pass
        data = default_data()
        save_data(data)
        return data


def save_data(data):
    data["last_saved"] = datetime.now().isoformat(timespec="seconds")
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(DATA_FILE)


def get_portfolio(data, portfolio_id):
    return next(p for p in data["portfolios"] if p["id"] == portfolio_id)


def portfolio_options(data):
    return {f'{p["name"]}  ·  #{p["id"]:02d}': p["id"] for p in data["portfolios"]}


# -----------------------------
# Auth
# -----------------------------
def get_secret(name, default=""):
    try:
        return str(st.secrets.get(name, default))
    except Exception:
        return default


def is_admin():
    return bool(st.session_state.get("admin_logged_in", False))


def login_box():
    st.sidebar.markdown("### 🔐 Admin Login")
    username = st.sidebar.text_input("Username", key="login_user")
    password = st.sidebar.text_input("Password", type="password", key="login_pass")

    if st.sidebar.button("Log in", use_container_width=True):
        expected_user = get_secret("ADMIN_USERNAME", "admin")
        expected_pass = get_secret("ADMIN_PASSWORD", "change-me")
        if hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass):
            st.session_state.admin_logged_in = True
            st.rerun()
        else:
            st.sidebar.error("Invalid login.")


# -----------------------------
# Portfolio calculations
# -----------------------------
def holding_frame(portfolio):
    rows = []
    for h in portfolio.get("holdings", []):
        qty = float(h.get("qty", 0))
        avg = float(h.get("avg_buy", 0))
        ltp = h.get("ltp")
        ltp = float(ltp) if ltp not in (None, "", "nan") else None
        invested = qty * avg
        current = qty * ltp if ltp is not None else None
        pnl = current - invested if current is not None else None
        pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None

        rows.append({
            "Stock": h.get("name") or h.get("ticker", ""),
            "Ticker": h.get("ticker", ""),
            "Qty": qty,
            "Avg. Buy": avg,
            "LTP": ltp,
            "Invested": invested,
            "Current Value": current,
            "P&L": pnl,
            "P&L %": pnl_pct,
        })
    return pd.DataFrame(rows)


def portfolio_metrics(portfolio):
    df = holding_frame(portfolio)
    invested = float(df["Invested"].sum()) if not df.empty else 0.0
    known = df["Current Value"].notna() if not df.empty else pd.Series(dtype=bool)
    current_known = float(df.loc[known, "Current Value"].sum()) if not df.empty else 0.0
    unknown_count = int((~known).sum()) if not df.empty else 0
    pnl = current_known - invested if unknown_count == 0 else None
    pnl_pct = (pnl / invested * 100) if pnl is not None and invested else None
    return invested, current_known, pnl, pnl_pct, unknown_count


# -----------------------------
# Price refresh (lazy)
# -----------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_prices(tickers):
    if not tickers:
        return {}
    import yfinance as yf

    result = {}
    for ticker in tickers:
        try:
            # fast_info is generally lighter than downloading a history table.
            t = yf.Ticker(ticker)
            price = t.fast_info.get("last_price")
            if price is not None:
                result[ticker] = float(price)
        except Exception:
            continue
    return result


def refresh_prices(portfolio):
    tickers = [h.get("ticker", "").strip() for h in portfolio.get("holdings", []) if h.get("ticker")]
    if not tickers:
        return 0
    prices = fetch_prices(tuple(sorted(set(tickers))))
    updated = 0
    for h in portfolio.get("holdings", []):
        ticker = h.get("ticker", "").strip()
        if ticker in prices:
            h["ltp"] = prices[ticker]
            h["price_updated"] = datetime.now().isoformat(timespec="seconds")
            updated += 1
    portfolio["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return updated


# -----------------------------
# UI
# -----------------------------
data = load_data()

with st.sidebar:
    st.markdown("## 📊 Portfolio Hub")
    st.caption("10 portfolios · unlimited holdings")
    st.divider()

    selected_label = st.selectbox(
        "Portfolio",
        list(portfolio_options(data).keys()),
        key="portfolio_selector",
    )
    selected_id = portfolio_options(data)[selected_label]

    page = st.radio(
        "Navigate",
        ["Portfolio", "Buy / Sell", "Performance", "Settings"],
        index=0,
        label_visibility="collapsed",
    )

    st.divider()

    if is_admin():
        st.success("Logged in as admin")
        if st.button("Log out", use_container_width=True):
            st.session_state.admin_logged_in = False
            st.rerun()
    else:
        login_box()

    if st.button("↻ Refresh Prices", use_container_width=True, disabled=not is_admin()):
        p = get_portfolio(data, selected_id)
        with st.spinner("Updating prices…"):
            n = refresh_prices(p)
        save_data(data)
        st.success(f"Updated {n} holding(s).")
        st.rerun()

    st.caption("Public mode: view only. Admin mode: edit portfolios and transactions.")

portfolio = get_portfolio(data, selected_id)

# Header
st.markdown('<div class="brand">PORTFOLIO HUB</div>', unsafe_allow_html=True)
st.markdown(f'<div class="title">{portfolio["name"]}</div>', unsafe_allow_html=True)
if portfolio.get("description"):
    st.caption(portfolio["description"])


# -----------------------------
# Portfolio page
# -----------------------------
if page == "Portfolio":
    invested, current, pnl, pnl_pct, unknown = portfolio_metrics(portfolio)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current Portfolio Value", f"₹{current:,.0f}" if unknown == 0 else "—")
    c2.metric("Capital Invested", f"₹{invested:,.0f}")
    c3.metric("Total P&L", f"₹{pnl:,.0f}" if pnl is not None else "—")
    c4.metric("Return", f"{pnl_pct:+.2f}%" if pnl_pct is not None else "—")

    if unknown:
        st.info(f"{unknown} holding(s) do not have a current price. Use “Refresh Prices” after logging in.")

    st.subheader("Holdings")
    df = holding_frame(portfolio)

    if df.empty:
        st.info("No holdings yet. Log in as admin to add stocks.")
    else:
        display = df.copy()
        for col in ["Avg. Buy", "LTP", "Invested", "Current Value", "P&L"]:
            display[col] = display[col].map(lambda x: f"₹{x:,.2f}" if pd.notna(x) else "—")
        display["Qty"] = display["Qty"].map(lambda x: f"{x:,.0f}")
        display["P&L %"] = display["P&L %"].map(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")
        st.dataframe(display, use_container_width=True, hide_index=True)

        st.subheader("Allocation")
        alloc = df.groupby("Stock", as_index=True)["Invested"].sum()
        if alloc.sum() > 0:
            st.bar_chart(alloc)

    st.divider()
    st.caption(
        "Educational / research-tracking portfolio. Values are simulated and are not investment advice."
    )


# -----------------------------
# Buy / Sell page
# -----------------------------
elif page == "Buy / Sell":
    st.subheader("Transactions")

    if not is_admin():
        st.info("Log in as admin to add or edit transactions.")
    else:
        with st.form("transaction_form", clear_on_submit=True):
            a, b, c, d = st.columns(4)
            ticker = a.text_input("Ticker", placeholder="RELIANCE.NS").upper().strip()
            name = b.text_input("Company", placeholder="Reliance Industries")
            action = c.selectbox("Action", ["BUY", "SELL"])
            qty = d.number_input("Quantity", min_value=0.0, step=1.0)

            e, f, g = st.columns(3)
            price = e.number_input("Price", min_value=0.0, step=0.05)
            trade_date = f.date_input("Date")
            note = g.text_input("Note", placeholder="Optional")

            submitted = st.form_submit_button("Save transaction", use_container_width=True)

        if submitted:
            if not ticker or qty <= 0 or price <= 0:
                st.error("Ticker, quantity and price are required.")
            else:
                existing = next(
                    (h for h in portfolio["holdings"] if h.get("ticker") == ticker),
                    None,
                )

                if action == "BUY":
                    if existing:
                        old_qty = float(existing["qty"])
                        old_avg = float(existing["avg_buy"])
                        new_qty = old_qty + qty
                        existing["avg_buy"] = ((old_qty * old_avg) + (qty * price)) / new_qty
                        existing["qty"] = new_qty
                        existing["ltp"] = price
                        if name:
                            existing["name"] = name
                    else:
                        portfolio["holdings"].append({
                            "ticker": ticker,
                            "name": name or ticker,
                            "qty": qty,
                            "avg_buy": price,
                            "ltp": price,
                            "price_updated": datetime.now().isoformat(timespec="seconds"),
                        })
                else:
                    if not existing or float(existing["qty"]) < qty:
                        st.error("Not enough quantity to sell.")
                        st.stop()

                    existing["qty"] = float(existing["qty"]) - qty
                    if existing["qty"] <= 0:
                        portfolio["holdings"].remove(existing)

                portfolio["transactions"].append({
                    "date": str(trade_date),
                    "ticker": ticker,
                    "name": name or ticker,
                    "action": action,
                    "qty": qty,
                    "price": price,
                    "note": note,
                })
                portfolio["updated_at"] = datetime.now().isoformat(timespec="seconds")
                save_data(data)
                st.success(f"{action} transaction saved.")
                st.rerun()

    tx = portfolio.get("transactions", [])
    if tx:
        tx_df = pd.DataFrame(tx).iloc[::-1]
        st.dataframe(tx_df, use_container_width=True, hide_index=True)
    else:
        st.caption("No transactions yet.")


# -----------------------------
# Performance page
# -----------------------------
elif page == "Performance":
    st.subheader("Performance")

    invested, current, pnl, pnl_pct, unknown = portfolio_metrics(portfolio)

    if unknown:
        st.info("Refresh prices to calculate current portfolio performance.")
    else:
        left, right = st.columns(2)
        left.metric("Invested", f"₹{invested:,.0f}")
        right.metric("Current Value", f"₹{current:,.0f}")

        if pnl is not None:
            st.metric("P&L", f"₹{pnl:,.0f}", f"{pnl_pct:+.2f}%")

        df = holding_frame(portfolio)
        if not df.empty:
            perf = df[["Stock", "Invested", "Current Value", "P&L", "P&L %"]].copy()
            st.dataframe(perf, use_container_width=True, hide_index=True)

    st.caption(
        "This version calculates position-level and portfolio-level returns from transactions. "
        "Historical NAV charts can be added once daily snapshots are stored."
    )


# -----------------------------
# Settings page
# -----------------------------
else:
    st.subheader("Portfolio Settings")

    if not is_admin():
        st.info("Log in as admin to edit settings.")
    else:
        with st.form("settings_form"):
            new_name = st.text_input("Portfolio name", value=portfolio["name"])
            new_desc = st.text_area("Description", value=portfolio.get("description", ""))
            save_settings = st.form_submit_button("Save changes", use_container_width=True)

        if save_settings:
            portfolio["name"] = new_name.strip() or f"Portfolio {portfolio['id']:02d}"
            portfolio["description"] = new_desc.strip()
            portfolio["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_data(data)
            st.success("Saved.")
            st.rerun()

        st.divider()
        st.caption(f"Portfolio ID: {portfolio['id']:02d}")
        st.caption(f"Last updated: {portfolio.get('updated_at', '—')}")
        st.caption(f"Data file: {DATA_FILE}")
