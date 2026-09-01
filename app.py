
import hmac
from datetime import datetime, date
from typing import Optional

import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.set_page_config(
    page_title="Capital Sights | Portfolio Hub",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------
# Styling
# -----------------------------
st.markdown("""
<style>
    .block-container {max-width: 1450px; padding-top: 1.2rem; padding-bottom: 3rem;}
    [data-testid="stMetric"] {
        background: rgba(255,255,255,.025);
        border: 1px solid rgba(255,255,255,.10);
        border-radius: 14px;
        padding: 15px;
    }
    .brand {font-size:.78rem; letter-spacing:.16em; color:#9da5b5; font-weight:700;}
    .hero {font-size:2.2rem; font-weight:800; margin:.1rem 0 .2rem;}
    .muted {color:#8d95a5;}
    .pill {display:inline-block; padding:4px 9px; border-radius:999px;
           background:rgba(214,168,79,.12); color:#d6a84f; font-size:.78rem;}
    div[data-testid="stButton"] > button {border-radius:9px;}
    .stDataFrame {border-radius:12px;}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Secrets / Supabase
# -----------------------------
def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

@st.cache_resource
def get_supabase() -> Client:
    # Supabase expects the bare project URL, e.g.
    # https://your-project.supabase.co
    # Users sometimes paste /rest/v1 or another API path from the dashboard;
    # normalize those suffixes so the client does not build an invalid URL.
    url = str(secret("SUPABASE_URL")).strip().rstrip("/")
    for suffix in ("/rest/v1", "/auth/v1", "/storage/v1", "/graphql/v1"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break

    key = str(secret("SUPABASE_SECRET_KEY") or secret("SUPABASE_SERVICE_ROLE_KEY")).strip()
    if not url or not key:
        raise RuntimeError(
            "Supabase is not configured. Add SUPABASE_URL and "
            "SUPABASE_SECRET_KEY (preferred) or SUPABASE_SERVICE_ROLE_KEY "
            "in Streamlit Secrets."
        )
    if not url.startswith("https://") or ".supabase.co" not in url:
        raise RuntimeError(
            "SUPABASE_URL must be the bare project URL, such as "
            "https://your-project.supabase.co"
        )
    return create_client(url, key)

def db():
    return get_supabase()

# -----------------------------
# Admin authentication
# -----------------------------
def is_admin():
    return bool(st.session_state.get("admin_logged_in"))

def login_sidebar():
    with st.sidebar:
        st.markdown("### 🔐 Admin Login")
        username = st.text_input("Username", key="admin_user")
        password = st.text_input("Password", type="password", key="admin_pass")
        if st.button("Log in", use_container_width=True):
            eu = secret("ADMIN_USERNAME", "admin")
            ep = secret("ADMIN_PASSWORD", "change-me")
            if hmac.compare_digest(username, eu) and hmac.compare_digest(password, ep):
                st.session_state.admin_logged_in = True
                st.rerun()
            else:
                st.error("Invalid credentials.")

# -----------------------------
# Database helpers
# -----------------------------
@st.cache_data(ttl=30, show_spinner=False)
def load_portfolios():
    rows = db().table("portfolios").select("*").order("portfolio_number").execute().data
    return rows

@st.cache_data(ttl=20, show_spinner=False)
def load_holdings(portfolio_id):
    return db().table("holdings").select("*").eq("portfolio_id", portfolio_id).order("ticker").execute().data

@st.cache_data(ttl=20, show_spinner=False)
def load_transactions(portfolio_id):
    return (
        db().table("transactions")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .order("trade_date", desc=True)
        .order("created_at", desc=True)
        .execute().data
    )

@st.cache_data(ttl=20, show_spinner=False)
def load_theses(portfolio_id):
    return (
        db().table("theses")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .order("ticker")
        .execute().data
    )

@st.cache_data(ttl=20, show_spinner=False)
def load_snapshots(portfolio_id):
    return (
        db().table("portfolio_snapshots")
        .select("*")
        .eq("portfolio_id", portfolio_id)
        .order("snapshot_date")
        .execute().data
    )

def clear_cache():
    load_portfolios.clear()
    load_holdings.clear()
    load_transactions.clear()
    load_theses.clear()
    load_snapshots.clear()

def current_portfolio(portfolios, number):
    return next(p for p in portfolios if p["portfolio_number"] == number)

def get_holding(holdings, ticker):
    return next((h for h in holdings if h["ticker"] == ticker), None)

def metrics_from_holdings(holdings):
    invested = sum(float(h.get("quantity",0)) * float(h.get("avg_buy_price",0)) for h in holdings)
    current = sum(
        float(h.get("quantity",0)) * float(h["ltp"])
        for h in holdings
        if h.get("ltp") is not None
    )
    missing = sum(1 for h in holdings if h.get("ltp") is None)
    pnl = current - invested if missing == 0 else None
    ret = pnl / invested * 100 if pnl is not None and invested else None
    return invested, current, pnl, ret, missing

# -----------------------------
# Prices — Google Finance via Google Sheets
# -----------------------------
def normalize_ticker(ticker):
    """Normalize common NSE ticker formats to a plain NSE symbol."""
    ticker = str(ticker or "").strip().upper()
    for suffix in (".NS", ":NSE"):
        if ticker.endswith(suffix):
            ticker = ticker[:-len(suffix)]
    return ticker


def google_symbol(ticker):
    """Return the Google Finance symbol used inside GOOGLEFINANCE()."""
    return f"NSE:{normalize_ticker(ticker)}"


def benchmark_symbol(ticker):
    """Normalize benchmark names/tickers for the Google Sheet feed."""
    value = str(ticker or "").strip().upper()
    if not value:
        return ""
    if value in {"NIFTY 50", "NIFTY50", "NSE:NIFTY50", "^NSEI"}:
        return "NIFTY50"
    return normalize_ticker(value)


def sheet_to_csv_url(sheet_url):
    """
    Accept the published Google Sheets URL supplied by the user.

    Examples accepted:
      https://docs.google.com/spreadsheets/d/e/.../pubhtml
      https://docs.google.com/spreadsheets/d/e/.../pub
      https://docs.google.com/spreadsheets/d/.../edit
      A direct CSV URL
    """
    url = str(sheet_url or "").strip()
    if not url:
        return ""

    # Already a CSV/export URL.
    if "output=csv" in url or "/export?format=csv" in url:
        return url

    # Published-to-web URL:
    # .../pubhtml  -> .../pub?output=csv
    if "/pubhtml" in url:
        return url.split("/pubhtml", 1)[0] + "/pub?output=csv"

    # Published URL without the HTML suffix.
    if re.search(r"/pub(?:\?|$)", url):
        return url.split("?", 1)[0] + "?output=csv"

    # Normal Google Sheets document URL.
    match = re.search(r"/spreadsheets/d/([^/]+)", url)
    if match:
        spreadsheet_id = match.group(1)
        return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv"

    return url


@st.cache_data(ttl=60, show_spinner=False)
def fetch_prices_from_google_sheet(sheet_url, tickers):
    """
    Read prices calculated by Google Sheets' GOOGLEFINANCE() function.

    The published Sheet must contain:
      Ticker | Google Symbol | Price

    Example:
      ADANIGREEN | NSE:ADANIGREEN | =GOOGLEFINANCE(B2,"price")
      JMFINANCIL | NSE:JMFINANCIL | =GOOGLEFINANCE(B3,"price")
    """
    if not sheet_url or not tickers:
        return {}

    csv_url = sheet_to_csv_url(sheet_url)

    try:
        df = pd.read_csv(csv_url)
        df.columns = [str(c).strip().lower() for c in df.columns]

        if "ticker" not in df.columns or "price" not in df.columns:
            raise RuntimeError(
                "Google Sheet must have columns named Ticker and Price."
            )

        prices = {}
        for _, row in df.iterrows():
            key = normalize_ticker(row.get("ticker"))
            wanted = {normalize_ticker(t) for t in tickers}

            # Also allow NIFTY50 to be stored as NIFTY 50 in the sheet.
            if key not in wanted and key.replace(" ", "") not in {
                x.replace(" ", "") for x in wanted
            }:
                continue

            raw = row.get("price")
            if pd.isna(raw):
                continue

            try:
                price = float(
                    str(raw).replace(",", "").replace("₹", "").strip()
                )
            except Exception:
                continue

            if price > 0:
                prices[key] = price

        result = {}
        for original in tickers:
            normalized = normalize_ticker(original)
            if normalized in prices:
                result[original] = prices[normalized]
            elif normalized.replace(" ", "") in {
                k.replace(" ", "") for k in prices
            }:
                match = next(
                    k for k in prices
                    if k.replace(" ", "") == normalized.replace(" ", "")
                )
                result[original] = prices[match]

        return result

    except Exception as e:
        raise RuntimeError(
            f"Could not read Google Finance prices from the published Sheet: {e}"
        )


def refresh_prices_and_snapshot(portfolio, holdings):
    tickers = sorted({h["ticker"] for h in holdings if h.get("ticker")})
    sheet_url = str(
        secret("GOOGLE_SHEET_URL") or secret("GOOGLE_SHEET_CSV_URL")
    ).strip()

    if not sheet_url:
        raise RuntimeError(
            "Google Finance is not configured. Add GOOGLE_SHEET_URL "
            "to Streamlit Secrets."
        )

    prices = fetch_prices_from_google_sheet(sheet_url, tuple(tickers))

    updated = 0
    for h in holdings:
        if h["ticker"] in prices:
            db().table("holdings").update({
                "ltp": prices[h["ticker"]],
                "price_updated_at": datetime.utcnow().isoformat()
            }).eq("id", h["id"]).execute()
            h["ltp"] = prices[h["ticker"]]
            updated += 1

    invested, current, pnl, ret, missing = metrics_from_holdings(holdings)

    if missing == 0:
        benchmark = benchmark_symbol(portfolio.get("benchmark_ticker"))
        benchmark_price = None

        if benchmark:
            # The Sheet can use either NIFTY50 or NIFTY 50.
            bp = fetch_prices_from_google_sheet(
                sheet_url,
                (benchmark, "NIFTY 50") if benchmark == "NIFTY50" else (benchmark,)
            )
            benchmark_price = (
                bp.get(benchmark)
                or bp.get("NIFTY 50")
            )

        db().table("portfolio_snapshots").upsert({
            "portfolio_id": portfolio["id"],
            "snapshot_date": str(date.today()),
            "portfolio_value": current,
            "invested_value": invested,
            "return_pct": ret,
            "benchmark_value": benchmark_price,
        }, on_conflict="portfolio_id,snapshot_date").execute()

    return updated


def auto_refresh_selected_portfolio(portfolio, holdings):
    """
    Refresh prices automatically when the app is opened/refreshed.
    Cached for 60 seconds so normal page navigation does not hammer the feed.
    """
    if not holdings:
        return 0

    try:
        return refresh_prices_and_snapshot(portfolio, holdings)
    except Exception:
        # Do not break the whole public dashboard if the external feed
        # is temporarily unavailable. The last stored LTP remains visible.
        return 0


def google_finance_setup():
    """Show the exact Google Sheet setup needed for live prices."""
    st.info(
        "The app reads the published Google Sheet automatically. "
        "Google Sheets calculates the market price with GOOGLEFINANCE()."
    )

    st.markdown("**Google Sheet columns**")
    st.code(
        "Ticker | Google Symbol | Price\n"
        "ADANIGREEN | NSE:ADANIGREEN | =GOOGLEFINANCE(B2,\"price\")\n"
        "JMFINANCIL | NSE:JMFINANCIL | =GOOGLEFINANCE(B3,\"price\")\n"
        "NIFTY50 | INDEXNSE:NIFTY_50 | =GOOGLEFINANCE(B4,\"price\")",
        language="text",
    )

    st.markdown(
        "In Streamlit Secrets, add `GOOGLE_SHEET_URL` and paste the "
        "published-to-web `/pubhtml` URL. The app converts it to CSV automatically."
    )


# -----------------------------
# Initial DB check
# -----------------------------
try:
    portfolios = load_portfolios()
except Exception as e:
    st.error("Database connection is not configured yet.")
    st.code(str(e))
    st.stop()

if len(portfolios) < 10:
    st.error("Your Supabase database needs 10 portfolio records. Run the supplied schema SQL.")
    st.stop()

# -----------------------------
# Sidebar
# -----------------------------
with st.sidebar:
    st.markdown("## 📊 Capital Sights")
    st.caption("10 portfolios · unlimited holdings")
    st.divider()

    labels = {
        f'Portfolio {p["portfolio_number"]:02d} · {p["name"]}': p["portfolio_number"]
        for p in portfolios
    }
    selected_label = st.selectbox("Portfolio", list(labels.keys()))
    selected_number = labels[selected_label]

    page = st.radio(
        "Navigate",
        ["Dashboard", "Portfolio", "Buy / Sell", "Transactions", "Performance", "Research", "Settings"],
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
        login_sidebar()

    if st.button("↻ Refresh Prices", use_container_width=True, disabled=not is_admin()):
        p = current_portfolio(portfolios, selected_number)
        hs = load_holdings(p["id"])
        fetch_prices_from_google_sheet.clear()

        try:
            with st.spinner("Fetching latest Google Finance prices…"):
                n = refresh_prices_and_snapshot(p, hs)
            clear_cache()
            if n:
                st.success(f"Updated {n} holding(s) from Google Finance.")
            else:
                st.warning(
                    "No matching Google Finance prices were returned. "
                    "Check the Ticker and Price columns in your Google Sheet."
                )
        except Exception as e:
            st.error("Price update failed.")
            st.code(str(e))

    st.caption("Public mode: view only. Admin mode: edit.")

portfolio = current_portfolio(portfolios, selected_number)
holdings = load_holdings(portfolio["id"])

# Automatically pull fresh prices from the published Google Sheet.
# This means you do not have to manually edit LTP values in Supabase.
if holdings:
    with st.spinner("Updating market prices…"):
        auto_refresh_selected_portfolio(portfolio, holdings)
    # Reload holdings so the just-updated LTP values are used everywhere.
    holdings = load_holdings(portfolio["id"])

transactions = load_transactions(portfolio["id"])

# -----------------------------
# Dashboard
# -----------------------------
if page == "Dashboard":
    st.markdown('<div class="brand">CAPITAL SIGHTS</div>', unsafe_allow_html=True)
    st.markdown('<div class="hero">Portfolio Dashboard</div>', unsafe_allow_html=True)
    st.caption("A single view across all 10 research portfolios.")

    total_invested = total_current = 0
    dashboard_rows = []
    for p in portfolios:
        hs = load_holdings(p["id"])
        inv, cur, pnl, ret, missing = metrics_from_holdings(hs)
        total_invested += inv
        total_current += cur
        dashboard_rows.append({
            "Portfolio": p["name"],
            "ID": f'{p["portfolio_number"]:02d}',
            "Invested": inv,
            "Current Value": cur if missing == 0 else None,
            "P&L": pnl,
            "Return": ret,
            "Holdings": len(hs),
        })

    total_pnl = total_current - total_invested
    total_ret = total_pnl / total_invested * 100 if total_invested else None

    a,b,c,d = st.columns(4)
    a.metric("Total Invested", f"₹{total_invested:,.0f}")
    b.metric("Current Value", f"₹{total_current:,.0f}")
    c.metric("Total P&L", f"₹{total_pnl:,.0f}")
    d.metric("Return", f"{total_ret:+.2f}%" if total_ret is not None else "—")

    st.subheader("All Portfolios")
    dd = pd.DataFrame(dashboard_rows)
    for col in ["Invested","Current Value","P&L"]:
        dd[col] = dd[col].map(lambda x: f"₹{x:,.0f}" if pd.notna(x) else "—")
    dd["Return"] = dd["Return"].map(lambda x: f"{x:+.2f}%" if pd.notna(x) else "—")
    st.dataframe(dd, use_container_width=True, hide_index=True)

# -----------------------------
# Portfolio
# -----------------------------
elif page == "Portfolio":
    st.markdown('<div class="brand">CAPITAL SIGHTS</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="hero">{portfolio["name"]}</div>', unsafe_allow_html=True)
    if portfolio.get("description"):
        st.caption(portfolio["description"])

    invested, current, pnl, ret, missing = metrics_from_holdings(holdings)
    a,b,c,d = st.columns(4)
    a.metric("Current Value", f"₹{current:,.0f}" if missing == 0 else "—")
    b.metric("Capital Invested", f"₹{invested:,.0f}")
    c.metric("Total P&L", f"₹{pnl:,.0f}" if pnl is not None else "—")
    d.metric("Return", f"{ret:+.2f}%" if ret is not None else "—")

    st.subheader("Holdings")
    if not holdings:
        st.info("No holdings yet. Log in as admin to add stocks.")
    else:
        rows=[]
        for h in holdings:
            inv=float(h["quantity"])*float(h["avg_buy_price"])
            cur=float(h["quantity"])*float(h["ltp"]) if h.get("ltp") is not None else None
            hpnl=cur-inv if cur is not None else None
            hpct=hpnl/inv*100 if hpnl is not None and inv else None
            rows.append({
                "Stock": h.get("company_name") or h["ticker"],
                "Ticker": h["ticker"],
                "Sector": h.get("sector") or "Unclassified",
                "Qty": h["quantity"],
                "Avg. Buy": inv / float(h["quantity"]) if h["quantity"] else 0,
                "LTP": h.get("ltp"),
                "Invested": inv,
                "Current Value": cur,
                "P&L": hpnl,
                "P&L %": hpct,
            })
        df=pd.DataFrame(rows)
        view=df.copy()
        for col in ["Avg. Buy","LTP","Invested","Current Value","P&L"]:
            view[col]=view[col].map(lambda x:f"₹{x:,.2f}" if pd.notna(x) else "—")
        view["P&L %"]=view["P&L %"].map(lambda x:f"{x:+.2f}%" if pd.notna(x) else "—")
        st.dataframe(view,use_container_width=True,hide_index=True)

        st.subheader("Sector Allocation")
        sec=df.groupby("Sector")["Invested"].sum().sort_values(ascending=False)
        if sec.sum():
            st.bar_chart(sec)

    st.divider()
    st.caption("Educational / research-tracking portfolio. Not investment advice.")

# -----------------------------
# Buy / Sell
# -----------------------------
elif page == "Buy / Sell":
    st.markdown(f'<div class="hero">{portfolio["name"]}</div>', unsafe_allow_html=True)
    st.subheader("Add transaction")
    if not is_admin():
        st.info("Log in as admin to add or edit transactions.")
    else:
        with st.form("trade_form", clear_on_submit=True):
            a,b,c,d=st.columns(4)
            ticker=a.text_input("Ticker",placeholder="RELIANCE.NS").upper().strip()
            company=b.text_input("Company")
            action=c.selectbox("Action",["BUY","SELL"])
            qty=d.number_input("Quantity",min_value=0.0,step=1.0)
            e,f,g,h=st.columns(4)
            price=e.number_input("Price",min_value=0.0,step=0.05)
            trade_date=f.date_input("Date",value=date.today())
            sector=g.text_input("Sector",placeholder="Financials")
            note=h.text_input("Note")
            submitted=st.form_submit_button("Save transaction",use_container_width=True)

        if submitted:
            if not ticker or qty<=0 or price<=0:
                st.error("Ticker, quantity and price are required.")
            else:
                existing=get_holding(holdings,ticker)
                if action=="BUY":
                    if existing:
                        old_qty=float(existing["quantity"])
                        old_avg=float(existing["avg_buy_price"])
                        new_qty=old_qty+qty
                        new_avg=((old_qty*old_avg)+(qty*price))/new_qty
                        db().table("holdings").update({
                            "quantity":new_qty,
                            "avg_buy_price":new_avg,
                            "ltp":price,
                            "company_name":company or existing.get("company_name") or ticker,
                            "sector":sector or existing.get("sector"),
                        }).eq("id",existing["id"]).execute()
                    else:
                        db().table("holdings").insert({
                            "portfolio_id":portfolio["id"],
                            "ticker":ticker,
                            "company_name":company or ticker,
                            "sector":sector or "Unclassified",
                            "quantity":qty,
                            "avg_buy_price":price,
                            "ltp":price,
                        }).execute()
                else:
                    if not existing or float(existing["quantity"])<qty:
                        st.error("Not enough quantity to sell.")
                        st.stop()
                    new_qty=float(existing["quantity"])-qty
                    if new_qty<=0:
                        db().table("holdings").delete().eq("id",existing["id"]).execute()
                    else:
                        db().table("holdings").update({"quantity":new_qty}).eq("id",existing["id"]).execute()

                db().table("transactions").insert({
                    "portfolio_id":portfolio["id"],
                    "trade_date":str(trade_date),
                    "ticker":ticker,
                    "company_name":company or ticker,
                    "action":action,
                    "quantity":qty,
                    "price":price,
                    "sector":sector or (existing.get("sector") if existing else "Unclassified"),
                    "note":note,
                }).execute()

                clear_cache()
                st.success(f"{action} saved.")
                st.rerun()

# -----------------------------
# Transactions
# -----------------------------
elif page == "Transactions":
    st.markdown(f'<div class="hero">{portfolio["name"]}</div>', unsafe_allow_html=True)
    st.subheader("Transaction History")
    if not transactions:
        st.info("No transactions yet.")
    else:
        tx=pd.DataFrame(transactions)
        cols=["trade_date","ticker","company_name","action","quantity","price","sector","note"]
        tx=tx[[c for c in cols if c in tx.columns]].rename(columns={
            "trade_date":"Date","ticker":"Ticker","company_name":"Company",
            "action":"Action","quantity":"Qty","price":"Price","sector":"Sector","note":"Note"
        })
        tx["Price"]=tx["Price"].map(lambda x:f"₹{float(x):,.2f}")
        st.dataframe(tx,use_container_width=True,hide_index=True)

# -----------------------------
# Performance
# -----------------------------
elif page == "Performance":
    st.markdown(f'<div class="hero">{portfolio["name"]}</div>', unsafe_allow_html=True)
    st.subheader("Historical Performance")

    snapshots=load_snapshots(portfolio["id"])
    if not snapshots:
        st.info("No daily snapshots yet. Refresh prices after the market data is available; snapshots will build over time.")
    else:
        s=pd.DataFrame(snapshots)
        s["snapshot_date"]=pd.to_datetime(s["snapshot_date"])
        s=s.sort_values("snapshot_date").set_index("snapshot_date")

        chart = s[["portfolio_value"]].rename(columns={"portfolio_value": portfolio["name"]}).copy()

        benchmark_name = portfolio.get("benchmark_ticker") or "NIFTY 50"

        # Benchmark values are stored in the daily Supabase snapshots.
        if "benchmark_value" in s.columns and s["benchmark_value"].notna().any():
            b = s["benchmark_value"].dropna()
            b_norm = b / float(b.iloc[0]) * 100
            p_norm = (
                chart.iloc[:, 0] / float(chart.iloc[0, 0]) * 100
                if float(chart.iloc[0, 0])
                else chart.iloc[:, 0]
            )
            comparison = pd.DataFrame({
                portfolio["name"]: p_norm,
                f"{benchmark_name} (100=base)": b_norm,
            }).sort_index().ffill()
            st.line_chart(comparison)
        else:
            st.line_chart(chart)

        latest=s.iloc[-1]
        a,b,c=st.columns(3)
        a.metric("Latest Value",f"₹{latest['portfolio_value']:,.0f}")
        a2 = float(s.iloc[0]["portfolio_value"]) if float(s.iloc[0]["portfolio_value"]) else 0
        portfolio_period = ((float(latest["portfolio_value"])/a2)-1)*100 if a2 else None
        b.metric("Portfolio Since First Snapshot",f"{portfolio_period:+.2f}%" if portfolio_period is not None else "—")

        bench_series = (
            s["benchmark_value"].dropna()
            if "benchmark_value" in s.columns
            else pd.Series(dtype=float)
        )
        if len(bench_series) >= 2:
            bench_return = (
                float(bench_series.iloc[-1]) / float(bench_series.iloc[0]) - 1
            ) * 100
            c.metric(f"{benchmark_name} Return", f"{bench_return:+.2f}%")
        else:
            c.metric(f"{benchmark_name} Return", "—")

        st.caption(
            f"Benchmark: {benchmark_name}. The comparison is normalized to 100 at the start "
            "of the available period. Historical portfolio points accumulate when prices are refreshed."
        )

# -----------------------------
# Research
# -----------------------------
elif page == "Research":
    st.markdown(f'<div class="hero">{portfolio["name"]}</div>', unsafe_allow_html=True)
    st.subheader("Investment Thesis")

    theses=load_theses(portfolio["id"])
    thesis_map={t["ticker"]:t for t in theses}

    tickers=[h["ticker"] for h in holdings]
    if not tickers:
        st.info("Add a holding first.")
    else:
        selected=st.selectbox("Stock",tickers)
        t=thesis_map.get(selected,{})
        if is_admin():
            with st.form("thesis_form"):
                why=st.text_area("Why we own it",value=t.get("why_we_own",""))
                bull=st.text_area("Bull case",value=t.get("bull_case",""))
                bear=st.text_area("Bear case",value=t.get("bear_case",""))
                risks=st.text_area("Key risks / things to monitor",value=t.get("key_risks",""))
                valuation=st.text_input("Valuation / target",value=t.get("valuation",""))
                save=st.form_submit_button("Save thesis",use_container_width=True)
            if save:
                payload={
                    "portfolio_id":portfolio["id"],"ticker":selected,
                    "why_we_own":why,"bull_case":bull,"bear_case":bear,
                    "key_risks":risks,"valuation":valuation,
                    "updated_at":datetime.utcnow().isoformat()
                }
                db().table("theses").upsert(payload,on_conflict="portfolio_id,ticker").execute()
                clear_cache()
                st.success("Thesis saved.")
                st.rerun()
        else:
            st.markdown(f"### {selected}")
            st.write("**Why we own it**")
            st.write(t.get("why_we_own") or "—")
            st.write("**Bull case**")
            st.write(t.get("bull_case") or "—")
            st.write("**Bear case**")
            st.write(t.get("bear_case") or "—")
            st.write("**Key risks**")
            st.write(t.get("key_risks") or "—")
            st.write("**Valuation / target**")
            st.write(t.get("valuation") or "—")

# -----------------------------
# Settings
# -----------------------------
else:
    st.markdown(f'<div class="hero">{portfolio["name"]}</div>', unsafe_allow_html=True)
    st.subheader("Portfolio Settings")
    if not is_admin():
        st.info("Log in as admin to edit portfolio settings.")
    else:
        with st.form("portfolio_settings"):
            name=st.text_input("Portfolio name",value=portfolio["name"])
            desc=st.text_area("Description",value=portfolio.get("description") or "")
            benchmark=st.text_input("Benchmark ticker / name",value=portfolio.get("benchmark_ticker") or "NIFTY 50")
            save=st.form_submit_button("Save changes",use_container_width=True)
        if save:
            db().table("portfolios").update({
                "name":name.strip() or portfolio["name"],
                "description":desc.strip(),
                "benchmark_ticker":benchmark.strip()
            }).eq("id",portfolio["id"]).execute()
            clear_cache()
            st.success("Portfolio updated.")
            st.rerun()

        st.divider()
        st.subheader("Google Finance Price Feed")
        google_finance_setup()
