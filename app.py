import streamlit as st
import streamlit.components.v1 as components
import plotly.graph_objects as go
from io import BytesIO
from datetime import datetime
import json
import hashlib
import os
import secrets  # for secure token generation

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

import extra_streamlit_components as stx  # cookies

# PDF generation
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

from openai import OpenAI


# =========================================================
# 0. OPENAI CLIENT & SYSTEM PROMPT
# =========================================================

def get_openai_client():
    """
    Load API key from Streamlit secrets or environment variables.
    Never hardcode it in the source code.
    """
    api_key = None
    try:
        api_key = st.secrets["OPENAI_API_KEY"]
    except Exception:
        pass

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OpenAI API key not found. Set OPENAI_API_KEY in Streamlit secrets or environment variables."
        )

    return OpenAI(api_key=api_key)


TERMSHEETGPT_SYSTEM_PROMPT = """
IDENTITY & PURPOSE

You are TermSheetGPT, an advanced AI assistant whose primary mission is to help founders negotiate significantly better term sheets.

Your focus is:

Identifying gaps, risks, deviations, and leverage points
Comparing terms against NVCA, YC SAFE, and Techstars seed standards
Providing practical, actionable negotiation moves
Supporting founders with clarity, not legal advice

You do not draft legal agreements.
You do not provide legal, tax, or financial advice.
You only provide educational and negotiation-focused guidance.

1. CORE OBJECTIVES

Your outputs must help the founder:

⭐ Negotiate a better deal

This is the primary goal—everything else is secondary.

⭐ Identify investor-friendly or aggressive clauses

Highlight:

Liquidation preference overreaches
Excessive dilution
Board control shifts
Anti-dilution traps
Missing founder protections
Unusual or off-market terms
SAFE/Note traps
Search fund promote & governance risks

⭐ Provide tactical, usable negotiation moves

For each clause:

Better alternative terms
Counter-asks
Market-standard wording (NVCA/SAFE)
Investor motivations
Questions that increase leverage
Practical founder-friendly language

⭐ Explain terms only when it helps negotiation

Explanations must be short and tied directly to negotiation.

2. DATA INPUT FORMAT

You will receive a single JSON object containing founder, company, round, traction, proposed terms, priorities, and investor context fields.

Some fields may be missing.
When missing:
→ Explicitly state your assumptions
→ Do not hallucinate or invent terms

If a clause is referenced but not provided:
→ Say: “To avoid inaccuracies, please paste the exact clause.”

3. STRICT RULES
❌ No hallucinating clauses
❌ No legal interpretations
❌ No guessing “standard” language
❌ No inventing missing SAFE/Note terms

If unclear:
→ Ask for the exact clause.

✔ All comparisons must use NVCA, SAFE, Techstars templates as baselines

4. INTERNAL PROCESS (DO NOT SHOW TO USER)

Internally, step through:

Understand context (stage, round, leverage, investor reputation)
Analyze valuation fairness + dilution impact
Analyze liquidation preference vs NVCA norms
Analyze dilution + option pool + convertible impact
Check control terms (board, voting, vetoes)
Detect deviations from NVCA/SAFE/Techstars
Tie everything back to founder’s stated priorities
Generate the strongest negotiation plan
Create clear, simple negotiation language founders can use

Do not reveal these steps.

5. OUTPUT FORMAT (MANDATORY)

Always return Markdown structured like this:

Deal Summary

1–2 short paragraphs summarizing:

Round type & amount
Stage & investor type
One-sentence verdict: Founder-friendly / Neutral / Investor-leaning / Red Flag

1. Valuation Analysis

What valuation implies (ownership %, dilution)
Whether valuation is Low / Fair / High for stage
Implications for founder control & future rounds

Recommended Valuation Negotiation Moves

Ask 1:
Ask 2:
Ask 3 (optional):

Example Language

Short 1–2 line phrases founders can use.

2. Liquidation Preference Analysis

Explain current preference in plain English
Compare to NVCA baseline
Classify: Founder-friendly / Market / Investor-aggressive / Red flag
Impact at small, medium, large exits

Negotiation Moves

(Concrete, specific asks)

Example Language

3. Dilution Analysis

Estimated dilution from this round
Additional dilution from option pool, SAFEs/notes
Long-term implications for Series A/B

Negotiation Moves

(3–6 specific tactics)

Example Language

4. Governance & Control (Board, Voting, Vetoes)

Identify any deviations from market standards
Highlight risks to founder control
Evaluate alignment with founder’s priorities

Negotiation Moves

5. SAFE / Convertible Note / Search Fund Terms (If applicable)

When SAFE/Notes present:

Compare valuation cap, discount, MFN to YC norms
Highlight missing terms
Show dilution risks

If Search Fund:

Analyze promote, structure, post-acquisition split
Compare to typical 25–30% promote models

Negotiation Moves

6. Alignment With Your Priorities

List founder’s top priorities (from JSON)
Show where current terms support vs conflict
Flag priority misalignment clearly

7. Your Top 3 Moves (Final Recommendation)

Move 1 — The single highest-impact negotiation strategy
Move 2
Move 3

End with:

This is educational guidance only and not legal advice. Always consult legal counsel before signing any term sheet.

6. TONE & STYLE

Your style must be:

Direct
Clear
Founder-first
Negotiation-focused
MBA + operator + VC associate tone
No legalese

Your goal is not to explain terms.
Your goal is to help founders win their negotiation.

7. WHEN A TERM SHEET TEXT IS UPLOADED

Begin with:

“I’ve received your document. I will compare it to NVCA and SAFE standards to identify risks and negotiation opportunities. Here is your negotiation-focused analysis.”

Then follow the mandatory output structure above.

8. FINANCIAL SIMULATION (WHEN INPUTS PROVIDED)

You may run:

Dilution modeling
Liquidation waterfall comparisons
Anti-dilution math
Founder payout scenarios

But only to support negotiation strategy.

If missing data:
→ Ask: “Please provide X so I can run the simulation.”

9. FORMATTING & TYPOGRAPHY RULES

Always use clean, normal Markdown formatting:
- Use spaces between numbers and words (write “5 million investment”, NOT “5millioninvestment”).
- Do NOT put each character or number on its own line.
- Avoid strange line breaks or vertical text.
- Use short paragraphs and bullet lists.
- Do not use LaTeX or math mode; just plain Markdown.

10. FINAL PROMISE

TermSheetGPT exists to help founders negotiate from a position of strength.
Not to define terms.
Not to explain law.
But to secure a better deal.
""".strip()


def build_json_payload(user_name: str, inputs: dict) -> dict:
    payload = {
        "founder": {
            "name": user_name,
        },
        "company": {
            "name": inputs.get("company_name"),
            "industry": inputs.get("industry"),
            "stage": inputs.get("stage"),
            "country": inputs.get("country"),
            "description": inputs.get("description"),
        },
        "round": {
            "round_label": inputs.get("round_label"),
            "round_type": inputs.get("instrument"),
            "currency": inputs.get("currency"),
            "pre_money_valuation": inputs.get("pre_money"),
            "investment_amount": inputs.get("investment_amount"),
            "equity_percentage": inputs.get("equity_percentage"),
            "assumed_exit_value": inputs.get("assumed_exit"),
        },
        "traction": {
            "annual_revenue": inputs.get("revenue"),
            "yoy_growth_percent": inputs.get("growth"),
        },
        "proposed_terms": {
            "liquidation_preference_multiple": inputs.get("liq_multiple"),
            "liquidation_preference_type": inputs.get("liq_type"),
            "anti_dilution_protection": inputs.get("anti_dilution"),
            "board_seats_for_investors": inputs.get("board_seats"),
            "board_terms_text": inputs.get("board_terms_text"),
            "veto_rights_text": inputs.get("veto_terms_text"),
            "safes_notes_details": inputs.get("safes_notes_details"),
            "option_pool_post_percent": inputs.get("option_pool_post"),
            "other_terms": inputs.get("other_terms"),
        },
        "priorities": {
            "valuation_priority_1_to_5": inputs.get("prio_valuation"),
            "dilution_priority_1_to_5": inputs.get("prio_dilution"),
            "control_priority_1_to_5": inputs.get("prio_control"),
            "speed_to_close_priority_1_to_5": inputs.get("prio_speed"),
            "notes": inputs.get("priority_notes"),
        },
        "investor_context": {
            "investor_type": inputs.get("investor_type"),
            "leverage": inputs.get("leverage"),
            "reputation": inputs.get("investor_reputation"),
        },
    }
    return payload


def call_termsheet_gpt_with_json(payload: dict) -> str:
    client = get_openai_client()
    user_content = (
        "Here is the deal context as a JSON object. "
        "Use it to perform the negotiation-focused analysis described in your instructions.\n\n"
        + json.dumps(payload, indent=2)
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": TERMSHEETGPT_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return f"⚠️ Error calling TermSheetGPT API: {e}"


# =========================================================
# 1. CONFIG & DB CONNECTION
# =========================================================

def get_db_config():
    db_host = st.secrets["DB_HOST"]
    db_user = st.secrets["DB_USER"]
    db_password = st.secrets["DB_PASSWORD"]
    db_name = st.secrets["DB_NAME"]
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
        # Add remember_token_hash column if it doesn't exist yet
        try:
            conn.execute(
                text("""
                    ALTER TABLE users
                    ADD COLUMN remember_token_hash VARCHAR(255) NULL
                """)
            )
        except Exception:
            pass

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
    except SQLAlchemyError:
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
        /* Pull the whole app higher and kill default Streamlit padding */
        .block-container {
            padding-top: 0.3rem !important;
            padding-bottom: 1.5rem;
            max-width: 1200px;
        }

        body {
            margin: 0;
        }

        .main {
            background-color: #020617;
            color: #f9fafb;
        }

        .ts-hero {
            padding: 1.5rem 1.8rem;
            border-radius: 18px;
            background: radial-gradient(circle at top left, #1d4ed8 0, #020617 55%, #020617 100%);
            border: 1px solid #1f2937;
            box-shadow: 0 18px 45px rgba(0,0,0,0.55);
        }
        .ts-hero-title {
            font-size: 1.8rem;
            font-weight: 700;
            letter-spacing: 0.02em;
        }
        .ts-hero-subtitle {
            margin-top: 0.4rem;
            color: #9ca3af;
            font-size: 0.95rem;
        }
        .ts-accent { color: #38bdf8; }
        .ts-subtle { color: #9ca3af; font-size: 0.9rem; }

        /* Bring auth section right under the navbar */
        .auth-wrapper {
            max-width: 1100px;
            margin: 0.3rem auto 1.2rem auto;  /* almost no top margin */
        }

        .auth-left-kicker {
            font-size: 0.85rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: #6b7280;
            margin-bottom: 0.4rem;
        }
        .auth-left-title {
            font-size: 1.5rem;
            font-weight: 600;
            margin-bottom: 0.35rem;
        }
        .auth-subcopy {
            font-size: 0.9rem;
            color: #9ca3af;
            margin-bottom: 0.75rem;
        }
        .auth-bullets {
            margin: 0.3rem 0 0.2rem 0;
            padding-left: 1.3rem;
            color: #d1d5db;
            font-size: 0.9rem;
        }
        .auth-bullets li {
            margin-bottom: 0.25rem;
        }
        .auth-pill {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            padding: 0.2rem 0.7rem;
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid rgba(148, 163, 184, 0.4);
            font-size: 0.75rem;
            color: #e5e7eb;
            margin-top: 0.6rem;
            margin-bottom: 0.9rem;
        }
        .auth-pill-dot {
            width: 7px;
            height: 7px;
            border-radius: 999px;
            background: #22c55e;
        }

        .stForm {
            background: rgba(15, 23, 42, 0.97);
            border-radius: 18px;
            padding: 1.6rem 1.7rem !important;
            border: 1px solid rgba(148, 163, 184, 0.35);
            box-shadow: 0 20px 55px rgba(0,0,0,0.65);
        }
        .stTabs {
            margin-top: 0.5rem;
        }
        .stTabs [role="tablist"] {
            gap: 0.5rem;
        }
        .stTabs [role="tab"] {
            padding: 0.25rem 0.9rem;
            border-radius: 999px;
            font-size: 0.9rem;
        }
        .ts-card {
            background-color: #020617;
            border-radius: 18px;
            padding: 1.5rem;
            border: 1px solid #1e293b;
            box-shadow: 0 0 30px rgba(0,0,0,0.35);
        }
        .key-moves-card {
            background-color: #0f172a;
            border-radius: 14px;
            padding: 1rem 1.25rem;
            border: 1px solid #1d4ed8;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# 4. FINANCE LOGIC & CHARTS
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
            x=["-20%", "Base", "+20%"],
            y=[pre * 0.8, pre, pre * 1.2],
            name="Pre-money valuation",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        title="Pre-money valuation sensitivity",
        yaxis_title=f"Pre-money ({currency})",
        yaxis=dict(tickformat=","),
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


def plot_ownership(pre, invest, equity_pct):
    if pre <= 0 or invest <= 0:
        return None

    post = pre + invest
    if equity_pct and equity_pct > 0:
        new_investor = equity_pct
    else:
        new_investor = invest / post * 100.0

    existing = max(100.0 - new_investor, 0.0)

    fig = go.Figure(
        data=[
            go.Pie(
                labels=["New investors", "Existing holders"],
                values=[new_investor, existing],
                hole=0.55,
            )
        ]
    )
    fig.update_layout(
        template="plotly_dark",
        title="Post-money ownership split",
        showlegend=True,
    )
    return fig


def plot_waterfall_scenarios(pre, invest, liq_mult, liq_type, equity, currency, base_exit):
    if base_exit <= 0:
        return None

    exits = [0.5 * base_exit, base_exit, 2.0 * base_exit]
    labels = [f"0.5× ({exits[0]:,.0f})", f"1.0× ({exits[1]:,.0f})", f"2.0× ({exits[2]:,.0f})"]
    inv_vals, fnd_vals = [], []

    for e in exits:
        inv, fnd = waterfall(pre, invest, liq_mult, liq_type, equity, e)
        inv_vals.append(inv)
        fnd_vals.append(fnd)

    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=labels,
            y=inv_vals,
            name="Investors",
        )
    )
    fig.add_trace(
        go.Bar(
            x=labels,
            y=fnd_vals,
            name="Founders / common",
        )
    )
    fig.update_layout(
        template="plotly_dark",
        barmode="stack",
        title=f"Liquidation waterfall across exit values ({currency})",
        yaxis_title=f"Proceeds ({currency})",
        yaxis=dict(tickformat=","),
    )
    return fig


# =========================================================
# 5. PDF EXPORT
# =========================================================

def _sanitize_for_pdf(text: str) -> str:
    if text is None:
        return ""
    try:
        return text.encode("latin-1", "replace").decode("latin-1")
    except Exception:
        return text.encode("ascii", "replace").decode("ascii")


def generate_pdf(summary_text: str, recommendations: str):
    if not FPDF_AVAILABLE:
        return None

    summary_text = _sanitize_for_pdf(summary_text)
    recommendations = _sanitize_for_pdf(recommendations)

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

    data = pdf.output(dest="S")
    if isinstance(data, str):
        pdf_bytes = data.encode("latin-1", "replace")
    else:
        pdf_bytes = bytes(data)

    buf = BytesIO(pdf_bytes)
    buf.seek(0)
    return buf


# =========================================================
# 6. AUTH UI (WITH REMEMBER-ME)
# =========================================================

def signin_form(cookie_manager):
    st.markdown("##### Sign in")
    st.caption("Sign in to continue refining your deal and negotiation strategy.")
    with st.form("signin"):
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        remember = st.checkbox("Keep me signed in on this device")
        ok = st.form_submit_button("Sign in")

    if ok:
        user = get_user_by_email(email)
        if user and verify_password(pw, user["password_hash"]):
            st.session_state["user"] = user

            if remember:
                raw_token = secrets.token_hex(32)
                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

                engine = get_engine()
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE users SET remember_token_hash = :th WHERE id = :uid"),
                        {"th": token_hash, "uid": user["id"]},
                    )

                cookie_manager.set(
                    "tsgpt_remember",
                    raw_token,
                    max_age=60 * 60 * 24 * 30,  # 30 days
                )

            st.success("You are now signed in.")
            st.rerun()
        else:
            st.error("Invalid email or password.")


def signup_form(cookie_manager):
    st.markdown("##### Create your TermSheetGPT account")
    st.caption("Save scenarios, compare rounds, and build a repeatable negotiation playbook.")
    with st.form("signup"):
        name = st.text_input("Name")
        email = st.text_input("Email")
        pw = st.text_input("Password", type="password")
        pw2 = st.text_input("Confirm password", type="password")
        remember = st.checkbox("Keep me signed in on this device")
        ok = st.form_submit_button("Sign up")

    if ok:
        if not name or not email or not pw:
            st.error("All fields are required.")
            return
        if pw != pw2:
            st.error("Passwords do not match.")
            return

        existing = get_user_by_email(email)
        if existing:
            st.error("An account with this email already exists. Please sign in instead.")
            return

        user = create_user(name, email, pw)
        if user:
            st.session_state["user"] = user

            if remember:
                raw_token = secrets.token_hex(32)
                token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                engine = get_engine()
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE users SET remember_token_hash = :th WHERE id = :uid"),
                        {"th": token_hash, "uid": user["id"]},
                    )
                cookie_manager.set(
                    "tsgpt_remember",
                    raw_token,
                    max_age=60 * 60 * 24 * 30,
                )

            st.success("Account created! You are now signed in.")
            st.rerun()
        else:
            st.error("Could not create account. Please try again.")


def render_auth_screen(cookie_manager):
    # Two-column layout: left = auth, right = hero / copy
    st.markdown("<div class='auth-wrapper'>", unsafe_allow_html=True)
    left_col, right_col = st.columns([1.1, 1.2])

    with left_col:
        st.markdown(
            """
            <div class="auth-left-kicker">Welcome back</div>
            <div class="auth-left-title">Log in or create your account.</div>
            <div class="auth-subcopy">
                Your information stays confidential. We only use it to help you negotiate a better deal.
            </div>
            """,
            unsafe_allow_html=True,
        )
        tabs = st.tabs(["Sign in", "Sign up"])
        with tabs[0]:
            signin_form(cookie_manager)
        with tabs[1]:
            signup_form(cookie_manager)

    with right_col:
        st.markdown(
            """
            <div class="ts-hero">
                <div class="ts-hero-title">
                    Founder-first guidance
                </div>
                <div class="ts-hero-subtitle">
                    Negotiate from a position of strength.
                </div>
                <div style="margin-top:0.9rem; font-size:0.9rem; color:#e5e7eb;">
                    TermSheetGPT turns messy term sheets into a focused negotiation plan
                    with concrete asks you can use in your next investor call.
                </div>
                <ul class="auth-bullets" style="margin-top:0.7rem;">
                    <li>Spot aggressive liquidation prefs, dilution and control traps in seconds.</li>
                    <li>Compare terms against NVCA, YC SAFE and Techstars-style norms.</li>
                    <li>Walk away with 2–3 founder-friendly moves to push for.</li>
                </ul>
                <div class="auth-pill">
                    <div class="auth-pill-dot"></div>
                    Built for early-stage founders raising their next round
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


# =========================================================
# 7. OUTPUT PARSING
# =========================================================

def extract_top_moves(text: str):
    if not text:
        return []
    lines = text.splitlines()
    moves = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if "Your Top 3 Moves" in stripped:
            in_section = True
            continue
        if in_section:
            if not stripped:
                if moves:
                    break
                else:
                    continue
            if (
                stripped.lower().startswith("move")
                or stripped.startswith("-")
                or stripped[:1].isdigit()
            ):
                moves.append(stripped)
    return moves[:3]


# =========================================================
# 8. MAIN APP
# =========================================================

def main():
    st.set_page_config(
        page_title="TermSheetGPT",
        layout="wide",
        page_icon="💼",
        initial_sidebar_state="collapsed",
    )
    inject_css()

    try:
        init_db()
    except Exception as e:
        st.error(f"Database connection failed: {e}")
        return

    # Cookie manager (no caching)
    cookie_manager = stx.CookieManager()

    if "user" not in st.session_state:
        st.session_state["user"] = None

    # Attempt auto-login from cookie if no user yet
    if st.session_state["user"] is None:
        cookies = cookie_manager.get_all() or {}
        raw_token = cookies.get("tsgpt_remember")
        if raw_token:
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
            engine = get_engine()
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT id, name, email FROM users WHERE remember_token_hash = :th"),
                    {"th": token_hash},
                ).fetchone()
            if row:
                st.session_state["user"] = {
                    "id": row[0],
                    "name": row[1],
                    "email": row[2],
                }
            else:
                cookie_manager.delete("tsgpt_remember")

    # If still no user, show auth screen
    if not st.session_state["user"]:
        render_auth_screen(cookie_manager)
        # Force scroll top
        components.html("<script>window.scrollTo(0, 0);</script>", height=0)
        return

    user = st.session_state["user"]
    name = user["name"]

    # Header with signout
    top_col1, top_col2 = st.columns([6, 1])
    with top_col1:
        st.markdown(
            f"""
            <div class="ts-card">
                <div style="font-size:0.8rem; text-transform:uppercase; letter-spacing:0.08em; color:#9ca3af;">
                    Negotiation Copilot
                </div>
                <h1 style="margin-top:0.3rem; margin-bottom:0.2rem;">
                    TermSheet<span class="ts-accent">GPT</span>
                </h1>
                <p class="ts-subtle">
                    {name}, let's map out your round and give you a clear, data-backed plan for your next investor conversation.
                </p>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with top_col2:
        st.write("")
        st.write("")
        if st.button("Sign out"):
            engine = get_engine()
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE users SET remember_token_hash = NULL WHERE id = :uid"),
                    {"uid": user["id"]},
                )
            cookie_manager.delete("tsgpt_remember")
            st.session_state["user"] = None
            st.rerun()

    st.write("")
    col1, col2 = st.columns([1.15, 0.85])

    # -------------------- LEFT: INPUTS --------------------
    with col1:
        with st.form("deal_form"):
            st.subheader("Company & Round Basics")

            company_name = st.text_input("Company name")
            industry = st.text_input("Industry / vertical")
            stage = st.selectbox("Company stage", ["Pre-revenue", "Pre-seed", "Seed", "Series A", "Series B", "Later"])
            round_label = st.text_input("Round label (e.g., Seed, Series A)", value="Series A")
            country = st.text_input("Country/Region", "United States")
            currency = st.selectbox("Currency", ["USD", "EUR", "GBP"])

            c1a, c1b = st.columns(2)
            with c1a:
                revenue_th = st.number_input(
                    "Annual revenue / ARR ('000)",
                    min_value=0,
                    value=0,
                    step=10,
                    format="%d",
                    help="Enter revenue in thousands. Example: 500 = 500,000."
                )
            with c1b:
                growth = st.number_input(
                    "YoY growth (%)",
                    min_value=-100.0,
                    value=50.0,
                    step=0.1,
                    format="%.1f",
                    help="Year-over-year revenue growth."
                )

            description = st.text_area(
                "Business description",
                height=80,
                help="Briefly describe your product, target customer, and traction."
            )

            st.markdown("---")
            st.subheader("Economics & Security")

            t1, t2 = st.columns(2)
            with t1:
                pre_money_th = st.number_input(
                    "Pre-money valuation ('000)",
                    min_value=0,
                    value=10_000,
                    step=500,
                    format="%d",
                    help="Pre-money valuation in thousands. Example: 10,000 = 10,000,000."
                )
                investment_amount_th = st.number_input(
                    "Investment amount ('000)",
                    min_value=0,
                    value=3_000,
                    step=250,
                    format="%d",
                    help="Investment amount in thousands. Example: 3,000 = 3,000,000."
                )
            with t2:
                equity_percentage = st.number_input(
                    "Equity % offered",
                    min_value=0.0,
                    max_value=100.0,
                    value=20.0,
                    step=1.0,
                    help="Percentage of fully diluted equity you are offering."
                )
                instrument = st.selectbox(
                    "Instrument",
                    ["Preferred Equity", "SAFE", "Convertible Note", "Common Equity"]
                )

            st.markdown("---")
            st.subheader("Key Investor Terms")

            t3, t4 = st.columns(2)
            with t3:
                liq_multiple = st.number_input(
                    "Liquidation preference multiple (x)",
                    min_value=0.5,
                    max_value=3.0,
                    value=1.0,
                    step=0.5,
                    help="How many times the investor's money returns before common."
                )
                liq_type = st.selectbox(
                    "Liquidation preference type",
                    ["Non-participating preferred", "Participating preferred"],
                    help="Non-participating vs participating preferred."
                )
            with t4:
                anti_dilution = st.selectbox(
                    "Anti-dilution protection",
                    ["None", "Broad-based weighted-average", "Narrow-based weighted-average", "Full ratchet"],
                    help="How investor price adjusts in a down round."
                )
                board_seats = st.number_input(
                    "Board seats for investors",
                    min_value=0,
                    value=1,
                    step=1,
                    help="Formal board seats granted to investors."
                )

            board_terms_text = st.text_area(
                "Board & control terms (optional)",
                height=60,
                help="Paste any relevant board composition / voting / control language (optional)."
            )

            veto_terms_text = st.text_area(
                "Veto / protective provisions (optional)",
                height=60,
                help="Paste key veto rights or protective provisions if you have them."
            )

            safes_notes_details = st.text_area(
                "Existing SAFEs / notes (optional)",
                height=60,
                help="Describe outstanding SAFEs/convertible notes, caps/discounts, or paste terms."
            )

            option_pool_post = st.number_input(
                "Post-money option pool target (%) (optional)",
                min_value=0.0,
                max_value=40.0,
                value=10.0,
                step=1.0,
                help="Rough target for ESOP post-money (if relevant)."
            )

            other_terms = st.text_area(
                "Other key terms / concerns",
                height=80,
                help="Anything else that matters in this negotiation (pro rata, MFN, information rights, etc.)."
            )

            st.markdown("---")
            st.subheader("Founder Priorities")

            pcol1, pcol2, pcol3, pcol4 = st.columns(4)
            with pcol1:
                prio_valuation = st.slider("Valuation", 1, 5, 4)
            with pcol2:
                prio_dilution = st.slider("Dilution", 1, 5, 4)
            with pcol3:
                prio_control = st.slider("Control", 1, 5, 5)
            with pcol4:
                prio_speed = st.slider("Speed to close", 1, 5, 3)

            priority_notes = st.text_area(
                "Anything else about your goals for this round? (optional)",
                height=60,
            )

            st.markdown("---")
            st.subheader("Investor Context")

            investor_type = st.selectbox(
                "Lead investor type",
                [
                    "Not specified",
                    "Top-tier VC",
                    "Emerging / new VC",
                    "Angel / super-angel",
                    "Strategic / Corporate",
                    "Family office / fund of funds",
                    "Other",
                ]
            )
            leverage = st.selectbox(
                "Who has more leverage right now?",
                ["Not specified", "Founder (multiple term sheets)", "Balanced", "Investor (few options)"]
            )
            investor_reputation = st.selectbox(
                "Investor reputation",
                ["Not specified", "Very strong / brand-name", "Good but not top-tier", "Unknown / mixed", "Potentially problematic"]
            )

            st.markdown("---")
            assumed_exit_th = st.slider(
                "Assumed exit value for waterfall ('000)",
                100,
                300_000,
                50_000,
                step=100,
                help="Exit value in thousands. Example: 50,000 = 50,000,000."
            )

            submitted = st.form_submit_button("Generate negotiation playbook")

        revenue = revenue_th * 1000.0
        pre_money = pre_money_th * 1000.0
        investment_amount = investment_amount_th * 1000.0
        assumed_exit = assumed_exit_th * 1000.0

        inputs = {
            "company_name": company_name,
            "industry": industry,
            "stage": stage,
            "round_label": round_label,
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
            "board_terms_text": board_terms_text,
            "veto_terms_text": veto_terms_text,
            "safes_notes_details": safes_notes_details,
            "option_pool_post": option_pool_post,
            "other_terms": other_terms,
            "assumed_exit": assumed_exit,
            "prio_valuation": prio_valuation,
            "prio_dilution": prio_dilution,
            "prio_control": prio_control,
            "prio_speed": prio_speed,
            "priority_notes": priority_notes,
            "investor_type": investor_type,
            "leverage": leverage,
            "investor_reputation": investor_reputation,
        }

    if submitted:
        save_deal(st.session_state["user"]["id"], inputs)
        payload = build_json_payload(name, inputs)
        with st.spinner("TermSheetGPT is analyzing your deal and building a negotiation plan..."):
            recs = call_termsheet_gpt_with_json(payload)
        st.session_state["recs"] = recs
        st.session_state["inputs"] = inputs

    # -------------------- RIGHT: OUTPUT --------------------
    with col2:
        st.subheader("AI Recommendations & Visuals")

        if "recs" in st.session_state:
            recs = st.session_state["recs"]
            deal = st.session_state["inputs"]

            moves = extract_top_moves(recs)
            if moves:
                st.markdown(
                    "<div class='key-moves-card'><b>Key Negotiation Moves</b></div>",
                    unsafe_allow_html=True,
                )
                for m in moves:
                    st.markdown(f"- {m}")
                st.write("")

            with st.expander("Full TermSheetGPT analysis", expanded=True):
                st.markdown(recs)

            st.markdown("##### Valuation sensitivity")
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

            st.markdown("##### Ownership / dilution")
            own_fig = plot_ownership(
                deal["pre_money"],
                deal["investment_amount"],
                deal["equity_percentage"],
            )
            if own_fig:
                st.plotly_chart(own_fig, use_container_width=True)
                st.caption(
                    f"New investors own ~{deal['equity_percentage']:.1f}% of the company post-money "
                    "(approximate, single-round view)."
                )

            st.markdown("##### Liquidation waterfall across exits")
            wf_fig_multi = plot_waterfall_scenarios(
                deal["pre_money"],
                deal["investment_amount"],
                deal["liq_multiple"],
                deal["liq_type"],
                deal["equity_percentage"],
                deal["currency"],
                deal["assumed_exit"],
            )
            if wf_fig_multi:
                st.plotly_chart(wf_fig_multi, use_container_width=True)
                st.caption(
                    "Stacked bars show how proceeds split between investors and founders/common "
                    "at downside (0.5×), base (1.0×), and upside (2.0×) exit values "
                    "(simplified single-round structure)."
                )

            st.markdown("##### Export as PDF")

            summary_text = f"""
Generated on: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

Company: {deal['company_name'] or 'N/A'}
Industry: {deal['industry'] or 'N/A'} | Stage: {deal['stage']} | Round: {deal['round_label']}
Country: {deal['country']} | Currency: {deal['currency']}

Pre-money valuation: {deal['pre_money']:,.0f}
Investment amount: {deal['investment_amount']:,.0f}
Equity offered: {deal['equity_percentage']:.1f}% ({deal['instrument']})

Liquidation preference: {deal['liq_multiple']}x ({deal['liq_type']})
Anti-dilution: {deal['anti_dilution']}
Board seats: {deal['board_seats']}
Option pool target (post): {deal['option_pool_post']:.1f}%

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

    # Always force scroll to top at the end of each run
    components.html("<script>window.scrollTo(0, 0);</script>", height=0)


if __name__ == "__main__":
    main()
