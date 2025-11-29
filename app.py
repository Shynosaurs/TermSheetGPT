
import streamlit as st
import plotly.graph_objects as go
import numpy as np
from io import BytesIO
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

import hashlib
import os

# PDF generation
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

# Optional: LLM integration placeholder
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# =========================================================
# 1. CONFIG & DB CONNECTION
# =========================================================

def get_db_config():
    # For Colab demo: use your AWS creds directly
    db_host = "isom599aws.c18k2ewikpxy.us-east-2.rds.amazonaws.com"
    db_user = "admin"
    db_password = "ISOM599db"
    db_name = "TermSheetGPT"
    return db_host, db_user, db_password, db_name


def get_engine():
    db_host, db_user, db_password, db_name = get_db_config()
    conn_str = (
        f"mysql+pymysql://{db_user}:{db_password}@{db_host}:3306/{db_name}"
        "?charset=utf8mb4"
    )
    return create_engine(conn_str, pool_pre_ping=True)


def init_db():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(
            text("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    name VARCHAR(255),
                    email VARCHAR(255) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
        )
        conn.execute(
            text("""
                CREATE TABLE IF NOT EXISTS deals (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    company_name VARCHAR(255),
                    industry VARCHAR(255),
                    stage VARCHAR(50),
                    country VARCHAR(100),
                    currency VARCHAR(10),
                    revenue DOUBLE,
                    growth DOUBLE,
                    description TEXT,
                    pre_money DOUBLE,
                    investment_amount DOUBLE,
                    equity_percentage DOUBLE,
                    instrument VARCHAR(50),
                    liq_multiple DOUBLE,
                    liq_type VARCHAR(100),
                    anti_dilution VARCHAR(100),
                    board_seats INT,
                    other_terms TEXT,
                    assumed_exit DOUBLE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)
        )
    return engine


# =========================================================
# 2. AUTH HELPERS
# =========================================================

def hash_password(password: str) -> str:
    """Simple salted SHA-256 hashing (demo only)."""
    salt = os.urandom(16).hex()
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, digest = stored_hash.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return candidate == digest


def get_user_by_email(email: str):
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT id, name, email, password_hash FROM users WHERE email = :email"),
            {"email": email},
        ).fetchone()

    if result:
        return {
            "id": result[0],
            "name": result[1],
            "email": result[2],
            "password_hash": result[3],
        }
    return None


def create_user(name: str, email: str, password: str):
    engine = get_engine()
    pwd_hash = hash_password(password)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO users (name, email, password_hash)
                    VALUES (:name, :email, :password_hash)
                """),
                {"name": name, "email": email, "password_hash": pwd_hash},
            )
        return get_user_by_email(email)
    except SQLAlchemyError as e:
        st.error(f"Database error: {e}")
        return None


def save_deal(user_id: int, inputs: dict):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO deals (
                    user_id, company_name, industry, stage, country, currency,
                    revenue, growth, description, pre_money, investment_amount,
                    equity_percentage, instrument, liq_multiple, liq_type,
                    anti_dilution, board_seats, other_terms, assumed_exit
                ) VALUES (
                    :user_id, :company_name, :industry, :stage, :country, :currency,
                    :revenue, :growth, :description, :pre_money, :investment_amount,
                    :equity_percentage, :instrument, :liq_multiple, :liq_type,
                    :anti_dilution, :board_seats, :other_terms, :assumed_exit
                )
            """),
            {
                "user_id": user_id,
                "company_name": inputs["company_name"],
                "industry": inputs["industry"],
                "stage": inputs["stage"],
                "country": inputs["country"],
                "currency": inputs["currency"],
                "revenue": inputs["revenue"],
                "growth": inputs["growth"],
                "description": inputs["description"],
                "pre_money": inputs["pre_money"],
                "investment_amount": inputs["investment_amount"],
                "equity_percentage": inputs["equity_percentage"],
                "instrument": inputs["instrument"],
                "liq_multiple": inputs["liq_multiple"],
                "liq_type": inputs["liq_type"],
                "anti_dilution": inputs["anti_dilution"],
                "board_seats": inputs["board_seats"],
                "other_terms": inputs["other_terms"],
                "assumed_exit": inputs["assumed_exit"],
            },
        )


# =========================================================
# 3. STYLE
# =========================================================

def inject_css():
    st.markdown(
        """
        <style>
        .main {
            background-color: #020617;
            color: #f9fafb;
        }
        .ts-card {
            background-color: #020617;
            border-radius: 18px;
            padding: 1.5rem;
            border: 1px solid #1e293b;
            box-shadow: 0 0 30px rgba(0,0,0,0.35);
        }
        .ts-accent { color: #38bdf8; }
        .ts-subtle { color: #9ca3af; font-size: 0.9rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# 4. LLM PROMPT & RETURN
# =========================================================

def build_termsheet_prompt(inputs: dict) -> str:
    return f"""
You are TermSheetGPT.

Company:
- Name: {inputs['company_name']}
- Industry: {inputs['industry']}
- Stage: {inputs['stage']}
- Country: {inputs['country']}
- ARR: {inputs['revenue']}
- Growth: {inputs['growth']}%

Deal:
- Pre-money: {inputs['pre_money']}
- Investment: {inputs['investment_amount']}
- Equity: {inputs['equity_percentage']}%
- Instrument: {inputs['instrument']}
- Liquidation: {inputs['liq_multiple']}x {inputs['liq_type']}
- Anti-dilution: {inputs['anti_dilution']}
- Board seats: {inputs['board_seats']}
- Other: {inputs['other_terms']}

Give negotiation guidance in bullet points, grouped as:
[Valuation], [Anti-Dilution], [Liquidation Preferences], [Other].
"""


def call_termsheet_gpt(prompt: str) -> str:
    # Placeholder so the app runs without an API key.
    return """
[Valuation]
- Anchor valuation using comparable deals, revenue, and growth.
- Offer modest movement on price in exchange for better terms (1x non-participating, broad-based weighted average).

[Anti-Dilution]
- Push for broad-based weighted-average anti-dilution.
- Avoid full ratchet, which can crush founder ownership in down rounds.

[Liquidation Preferences]
- Target 1x non-participating as the default.
- If investors want participation, request a cap at 2–3x and negotiate for participation to fall away at higher exits.

[Other]
- Maintain at least parity on the board; avoid giving up control too early.
- Narrow protective provisions to truly major events (M&A, new senior securities, changes to charter).
- Align information rights with what you can deliver without excessive overhead.
    """.strip()


# =========================================================
# 5. FINANCE LOGIC & CHARTS
# =========================================================

def implied_revenue_multiple(pre: float, rev: float):
    if rev and rev > 0:
        return pre / rev
    return None


def plot_valuation(pre: float, currency: str):
    if pre <= 0:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=["Downside", "Base", "Upside"],
            y=[pre * 0.8, pre, pre * 1.2],
            name="Valuation scenarios",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        title=f"Valuation Scenarios ({currency})",
        yaxis_title=f"Pre-money valuation ({currency})",
        showlegend=False,
    )
    return fig


def waterfall(pre, invest, liq_mult, liq_type, equity, exit_v):
    if pre <= 0 or invest <= 0 or liq_mult <= 0 or exit_v <= 0:
        return 0.0, 0.0

    post = pre + invest
    owner = equity / 100.0 if equity > 0 else invest / post
    pref = invest * liq_mult

    liq_type_lower = liq_type.lower()
    if "non-participating" in liq_type_lower:
        pro_rata = owner * exit_v
        investor_payout = min(max(pref, pro_rata), exit_v)
        founder_payout = max(exit_v - investor_payout, 0.0)
    else:
        remaining = max(exit_v - pref, 0.0)
        investor_extra = owner * remaining
        investor_payout = min(pref + investor_extra, exit_v)
        founder_payout = max(exit_v - investor_payout, 0.0)

    return investor_payout, founder_payout


def plot_waterfall(pre, invest, liq_mult, liq_type, equity, currency, exit_v):
    inv, fnd = waterfall(pre, invest, liq_mult, liq_type, equity, exit_v)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=["Investors"], y=[inv], name="Investors"))
    fig.add_trace(go.Bar(x=["Founders"], y=[fnd], name="Founders/Common"))
    fig.update_layout(
        template="plotly_dark",
        barmode="stack",
        title=f"Waterfall at {exit_v:,.0f} {currency} exit",
        yaxis_title=f"Proceeds ({currency})",
    )
    return fig, inv, fnd


# =========================================================
# 6. PDF EXPORT
# =========================================================

def generate_pdf(summary_text: str, recommendations: str):
    if not FPDF_AVAILABLE:
        return None

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 8, "TermSheetGPT Negotiation Summary", ln=True)

    pdf.ln(4)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(0, 6, summary_text)

    pdf.ln(4)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(0, 8, "AI Recommendations", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.multi_cell(0, 6, recommendations)

    buf = BytesIO()
    pdf.output(buf, "F")
    buf.seek(0)
    return buf


# =========================================================
# 7. AUTH UI
# =========================================================

def signup_form():
    st.subheader("Sign up")
    with st.form("signup"):
        name = st.text_input("Name")
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        pw2 = st.text_input("Confirm Password", type="password")
        ok = st.form_submit_button("Create Account")
    if ok:
        if not name or not email or not pw:
            st.error("All fields are required.")
            return
        if pw != pw2:
            st.error("Passwords do not match.")
            return
        user = create_user(name, email, pw)
        if user:
            st.session_state["user"] = user
            st.success("Account created!")


def signin_form():
    st.subheader("Sign in")
    with st.form("signin"):
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")
    if ok:
        user = get_user_by_email(email)
        if user and verify_password(pw, user["password_hash"]):
            st.session_state["user"] = user
            st.success("Logged in!")
        else:
            st.error("Invalid credentials.")


# =========================================================
# 8. MAIN APP
# =========================================================

def main():
    st.set_page_config(page_title="TermSheetGPT", layout="wide", page_icon="💼")
    inject_css()

    try:
        init_db()
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return

    if "user" not in st.session_state:
        st.session_state["user"] = None

    # Sidebar
    with st.sidebar:
        st.title("TermSheetGPT")
        if st.session_state["user"]:
            st.write(f"Signed in as: {st.session_state['user']['name']}")
            if st.button("Sign out"):
                st.session_state["user"] = None
        else:
            tab = st.radio("Account", ["Sign in", "Sign up"])
            if tab == "Sign in":
                signin_form()
            else:
                signup_form()

        st.markdown("---")
        st.markdown(
            "<span class='ts-subtle'>Prototype; do not store sensitive data. "
            "For production, move DB credentials to secrets.</span>",
            unsafe_allow_html=True,
        )

    if not st.session_state["user"]:
        st.info("Please sign in to use the app.")
        return

    st.markdown(
        """
        <div class="ts-card">
            <div style="font-size:0.8rem; text-transform:uppercase; letter-spacing:0.08em; color:#9ca3af;">
                Negotiation Copilot
            </div>
            <h1 style="margin-top:0.3rem; margin-bottom:0.2rem;">
                TermSheet<span class="ts-accent">GPT</span>
            </h1>
            <p class="ts-subtle">
                Structure your term sheet, understand trade-offs around valuation, anti-dilution,
                and liquidation preferences, and walk into investor conversations with a clear
                negotiation playbook.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    col1, col2 = st.columns([1.1, 1.0])

    with col1:
        with st.form("deal_form"):
            st.subheader("Deal & Company Inputs")

            company_name = st.text_input("Company name")
            industry = st.text_input("Industry / vertical")
            stage = st.selectbox("Stage", ["Pre-seed", "Seed", "Series A", "Series B", "Later"])
            country = st.text_input("Country/Region", "United States")
            currency = st.selectbox("Currency", ["USD", "EUR", "GBP", "Other"])

            c1, c2 = st.columns(2)
            with c1:
                revenue = st.number_input("Annual revenue / ARR", min_value=0.0, value=0.0, step=10000.0)
            with c2:
                growth = st.number_input("YoY growth (%)", min_value=-100.0, value=50.0, step=5.0)

            description = st.text_area("Business description", height=80)

            st.markdown("---")
            st.subheader("Term Sheet Terms")

            t1, t2 = st.columns(2)
            with t1:
                pre_money = st.number_input("Pre-money valuation", min_value=0.0, value=10_000_000.0, step=500_000.0)
                investment_amount = st.number_input("Investment amount", min_value=0.0, value=3_000_000.0, step=250_000.0)
            with t2:
                equity_percentage = st.number_input("Equity % offered", min_value=0.0, max_value=100.0, value=20.0, step=1.0)
                instrument = st.selectbox("Instrument", ["Preferred Equity", "SAFE", "Convertible Note", "Common Equity"])

            t3, t4 = st.columns(2)
            with t3:
                liq_multiple = st.number_input("Liquidation pref multiple (x)", min_value=0.5, max_value=3.0, value=1.0, step=0.5)
                liq_type = st.selectbox("Liquidation pref type", ["Non-participating preferred", "Participating preferred"])
            with t4:
                anti_dilution = st.selectbox(
                    "Anti-dilution protection",
                    ["None", "Broad-based weighted-average", "Narrow-based weighted-average", "Full ratchet"]
                )
                board_seats = st.number_input("Board seats for investors", min_value=0, value=1, step=1)

            other_terms = st.text_area("Other key terms / concerns", height=80)

            st.markdown("---")
            assumed_exit = st.slider("Assumed exit value for waterfall", 5_000_000, 200_000_000, 50_000_000, step=5_000_000)

            submitted = st.form_submit_button("Generate negotiation playbook")

        inputs = {
            "company_name": company_name,
            "industry": industry,
            "stage": stage,
            "country": country,
            "currency": currency,
            "revenue": revenue,
            "growth": growth,
            "description": description,
            "pre_money": pre_money,
            "investment_amount": investment_amount,
            "equity_percentage": equity_percentage,
            "instrument": instrument,
            "liq_multiple": liq_multiple,
            "liq_type": liq_type,
            "anti_dilution": anti_dilution,
            "board_seats": board_seats,
            "other_terms": other_terms,
            "assumed_exit": assumed_exit,
        }

    if submitted:
        save_deal(st.session_state["user"]["id"], inputs)
        prompt = build_termsheet_prompt(inputs)
        recs = call_termsheet_gpt(prompt)
        st.session_state["recs"] = recs
        st.session_state["inputs"] = inputs

    with col2:
        st.subheader("AI Recommendations & Visuals")

        if "recs" in st.session_state:
            recs = st.session_state["recs"]
            deal = st.session_state["inputs"]

            st.markdown("##### Negotiation Guidance")
            st.write(recs)

            st.markdown("##### Valuation Scenarios")
            val_fig = plot_valuation(deal["pre_money"], deal["currency"])
            if val_fig:
                st.plotly_chart(val_fig, use_container_width=True)
                mult = implied_revenue_multiple(deal["pre_money"], deal["revenue"])
                if mult:
                    st.caption(
                        f"Implied pre-money revenue multiple: **{mult:.1f}x** "
                        f"(pre-money {deal['pre_money']:,.0f} / revenue {deal['revenue']:,.0f})."
                    )
            else:
                st.caption("Enter a positive pre-money valuation to see scenarios.")

            st.markdown("##### Liquidation Waterfall")
            wf_fig, inv_p, fnd_p = plot_waterfall(
                deal["pre_money"],
                deal["investment_amount"],
                deal["liq_multiple"],
                deal["liq_type"],
                deal["equity_percentage"],
                deal["currency"],
                deal["assumed_exit"],
            )
            st.plotly_chart(wf_fig, use_container_width=True)
            st.caption(
                f"At a {deal['assumed_exit']:,.0f} {deal['currency']} exit: "
                f"Investors ≈ {inv_p:,.0f}, Founders/Common ≈ {fnd_p:,.0f} "
                "(simplified one-round structure)."
            )

            st.markdown("##### Export as PDF")

            summary_text = f"""
Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

Company: {deal['company_name'] or 'N/A'}
Industry: {deal['industry'] or 'N/A'} | Stage: {deal['stage']}
Country: {deal['country']} | Currency: {deal['currency']}

Pre-money valuation: {deal['pre_money']:,.0f}
Investment amount: {deal['investment_amount']:,.0f}
Equity offered: {deal['equity_percentage']:.1f}% ({deal['instrument']})

Liquidation preference: {deal['liq_multiple']}x ({deal['liq_type']})
Anti-dilution: {deal['anti_dilution']}
Board seats: {deal['board_seats']}

Assumed exit (for visuals): {deal['assumed_exit']:,.0f}
"""

            pdf_buf = generate_pdf(summary_text, recs)
            if pdf_buf:
                st.download_button(
                    "Download PDF",
                    pdf_buf,
                    file_name="TermSheetGPT_summary.pdf",
                    mime="application/pdf",
                )
            else:
                st.caption("Install `fpdf2` to enable PDF export.")


if __name__ == "__main__":
    main()
