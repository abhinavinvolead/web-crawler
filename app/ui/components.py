import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# Curated harmonious color palette for brands
BRAND_COLORS = [
    "#7C3AED",  # electric violet
    "#0EA5E9",  # ocean sky
    "#10B981",  # emerald green
    "#F59E0B",  # warm amber
    "#EC4899",  # rose pink
    "#6366F1",  # royal indigo
    "#14B8A6",  # cyan teal
    "#F97316",  # vivid orange
]

def inject_premium_css():
    st.markdown("""
    <style>
    /* ── Typography & Base ─────────────────────────────────────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    
    html, body, .stMarkdown, p, h1, h2, h3, h4, h5, h6, label, input, textarea, select, button {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* ── Protect Streamlit ligature icon fonts & expander toggle arrows ── */
    [data-testid*="Icon"],
    [data-testid="stExpanderToggleIcon"],
    [data-testid="stIcon"],
    .material-icons,
    .material-symbols-rounded {
        font-family: inherit !important;
    }

    /* Ensure expander header items flex smoothly without text overlapping */
    div[data-testid="stExpander"] details summary {
        display: flex !important;
        align-items: center !important;
        gap: 0.5rem !important;
    }
    div[data-testid="stExpanderToggleIcon"] {
        margin-right: 0.5rem !important;
        display: inline-flex !important;
        align-items: center !important;
    }

    /* ── Gradient Page Title ───────────────────────────────────────── */
    h1 {
        background: linear-gradient(135deg, #A78BFA 0%, #38BDF8 50%, #34D399 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        font-weight: 800 !important;
        letter-spacing: -0.03em;
        margin-bottom: 0.25rem !important;
    }
    h2, h3 {
        font-weight: 600 !important;
        letter-spacing: -0.02em;
    }

    /* ── Metric Glassmorphic Cards ─────────────────────────────────── */
    div[data-testid="metric-container"] {
        background: rgba(255, 255, 255, 0.035);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 14px;
        padding: 1.15rem 1.35rem;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1), 
                    border-color 0.22s ease, 
                    box-shadow 0.22s ease;
    }
    div[data-testid="metric-container"]:hover {
        transform: translateY(-3px);
        border-color: rgba(167, 139, 250, 0.35);
        box-shadow: 0 8px 30px rgba(124, 58, 237, 0.18);
    }
    div[data-testid="metric-container"] label {
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        color: rgba(255, 255, 255, 0.75) !important;
    }
    div[data-testid="metric-container"] [data-testid="stMetricValue"] {
        font-weight: 700 !important;
        font-size: 1.6rem !important;
        letter-spacing: -0.02em;
    }

    /* ── Interactive Buttons & Primary Actions ─────────────────────── */
    div[data-testid="stFormSubmitButton"] > button,
    button[kind="primary"] {
        background: linear-gradient(135deg, #7C3AED 0%, #0EA5E9 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        padding: 0.6rem 1.4rem !important;
        box-shadow: 0 4px 14px rgba(124, 58, 237, 0.35) !important;
        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    div[data-testid="stFormSubmitButton"] > button:hover,
    button[kind="primary"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px rgba(14, 165, 233, 0.45) !important;
        filter: brightness(1.08) !important;
    }
    div[data-testid="stFormSubmitButton"] > button:active,
    button[kind="primary"]:active {
        transform: translateY(0px) !important;
    }

    /* ── Secondary & Outline Buttons ──────────────────────────────── */
    button[kind="secondary"] {
        border-radius: 10px !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
        background: rgba(255, 255, 255, 0.04) !important;
        transition: all 0.18s ease !important;
    }
    button[kind="secondary"]:hover {
        border-color: rgba(255, 255, 255, 0.35) !important;
        background: rgba(255, 255, 255, 0.08) !important;
        transform: translateY(-1px) !important;
    }

    /* ── Form Inputs & Selectboxes ────────────────────────────────── */
    div[data-baseweb="input"],
    div[data-baseweb="textarea"],
    div[data-baseweb="select"] {
        border-radius: 10px !important;
        border-color: rgba(255, 255, 255, 0.12) !important;
        transition: border-color 0.2s ease, box-shadow 0.2s ease !important;
    }
    div[data-baseweb="input"]:focus-within,
    div[data-baseweb="textarea"]:focus-within {
        border-color: #8B5CF6 !important;
        box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.25) !important;
    }

    /* ── Tabs Styling ─────────────────────────────────────────────── */
    button[data-baseweb="tab"] {
        font-weight: 600 !important;
        padding: 0.75rem 1.25rem !important;
        border-radius: 8px 8px 0 0 !important;
        transition: color 0.15s ease !important;
    }

    /* ── Expanders ────────────────────────────────────────────────── */
    div[data-testid="stExpander"] {
        border-radius: 12px !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        background: rgba(255, 255, 255, 0.02) !important;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15) !important;
        margin-bottom: 0.75rem !important;
    }

    /* ── Progress Bar ─────────────────────────────────────────────── */
    div[data-testid="stProgressBar"] > div > div {
        background: linear-gradient(90deg, #7C3AED, #0EA5E9, #10B981) !important;
        border-radius: 6px !important;
    }

    /* ── DataFrames ───────────────────────────────────────────────── */
    div[data-testid="stDataFrame"] {
        border-radius: 12px !important;
        overflow: hidden !important;
        border: 1px solid rgba(255, 255, 255, 0.07) !important;
    }

    /* ── Pulsing Active Run Indicator ─────────────────────────────── */
    @keyframes pulse-dot {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.45; transform: scale(0.92); }
    }
    .pulsing-indicator {
        display: inline-block;
        animation: pulse-dot 1.8s infinite ease-in-out;
    }

    /* ── Padding & Layout Cleanliness ─────────────────────────────── */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 3.5rem !important;
        max-width: 1250px !important;
    }
    </style>
    """, unsafe_allow_html=True)


def plot_sentiment_trend(brands_data: list[dict]):
    if not brands_data:
        return None

    df_rows = []
    for b in brands_data:
        brand_name = b["brand"]
        for ym, val in b.get("monthly_trend", {}).items():
            if val is not None:
                df_rows.append({"Brand": brand_name, "Month": str(ym), "Sentiment": float(val)})

    if not df_rows:
        return None

    df = pd.DataFrame(df_rows)
    df = df.sort_values("Month")

    fig = px.line(
        df,
        x="Month",
        y="Sentiment",
        color="Brand",
        markers=True,
        title="<b>Monthly Sentiment Trajectory</b>",
        color_discrete_sequence=BRAND_COLORS,
        template="plotly_dark",
    )
    fig.update_traces(
        line=dict(width=2.8, shape="spline"),
        marker=dict(size=8, line=dict(width=1.5, color="rgba(255,255,255,0.7)")),
        hovertemplate="<b>%{data.name}</b><br>Period: %{x}<br>Sentiment: %{y:+.2f}<extra></extra>",
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        margin=dict(l=40, r=30, t=55, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
            title="",
            font=dict(size=12),
        ),
        font=dict(family="Inter, sans-serif"),
    )
    fig.update_yaxes(
        range=[-1.05, 1.05],
        gridcolor="rgba(255,255,255,0.08)",
        zeroline=True,
        zerolinecolor="rgba(255,255,255,0.25)",
        zerolinewidth=1.2,
    )
    fig.update_xaxes(gridcolor="rgba(255,255,255,0.08)")
    return fig


def plot_share_of_voice(brands_data: list[dict]):
    if not brands_data:
        return None
    df = pd.DataFrame([{"Brand": b["brand"], "Mentions": b.get("mentions", 0)} for b in brands_data])
    if df.empty or df["Mentions"].sum() == 0:
        return None

    fig = px.pie(
        df,
        values="Mentions",
        names="Brand",
        hole=0.62,
        title="<b>Share of Voice (Item Mentions)</b>",
        color_discrete_sequence=BRAND_COLORS,
        template="plotly_dark",
    )
    fig.update_traces(
        textposition="inside",
        textinfo="percent+label",
        hovertemplate="<b>%{label}</b><br>Mentions: %{value:,}<br>Share: %{percent}<extra></extra>",
        marker=dict(line=dict(color="rgba(0,0,0,0.5)", width=2)),
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=55, b=30, l=30, r=30),
        showlegend=False,
        font=dict(family="Inter, sans-serif"),
    )
    return fig


def plot_rating_distribution(reviews: list[dict]):
    if not reviews:
        return None
    ratings = [int(round(r["rating"])) for r in reviews if r.get("rating") is not None and 1 <= round(r["rating"]) <= 5]
    if not ratings:
        return None
    counts = {star: ratings.count(star) for star in range(1, 6)}
    df = pd.DataFrame([{"Stars": f"{star} ⭐", "Reviews": counts[star]} for star in range(1, 6)])
    colors = ["#EF4444", "#F97316", "#FBBF24", "#34D399", "#10B981"]
    fig = px.bar(
        df,
        x="Stars",
        y="Reviews",
        text="Reviews",
        title="<b>Star Rating Distribution</b>",
        color="Stars",
        color_discrete_sequence=colors,
        template="plotly_dark",
    )
    fig.update_traces(
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>Reviews: %{y:,}<extra></extra>",
        marker=dict(line=dict(width=1, color="rgba(255,255,255,0.2)")),
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        showlegend=False,
        margin=dict(t=50, b=30, l=30, r=30),
        font=dict(family="Inter, sans-serif"),
    )
    return fig


def plot_aspect_breakdown(mentions: list[dict]):
    if not mentions:
        return None
    from collections import defaultdict
    aspect_scores = defaultdict(list)
    for m in mentions:
        asp = m.get("aspect")
        if asp and asp != "general":
            aspect_scores[asp].append(m.get("sentiment", 0))
    if not aspect_scores:
        return None
    rows = []
    for asp, sents in aspect_scores.items():
        avg_s = sum(sents) / len(sents)
        rows.append({"Aspect": asp.replace("_", " ").title(), "Sentiment": round(avg_s, 2), "Mentions": len(sents)})
    df = pd.DataFrame(rows).sort_values("Sentiment", ascending=True)
    fig = px.bar(
        df,
        x="Sentiment",
        y="Aspect",
        orientation="h",
        text="Sentiment",
        color="Sentiment",
        color_continuous_scale=["#EF4444", "#F59E0B", "#10B981"],
        range_color=[-1, 1],
        title="<b>Aspect Sentiment Breakdown (-1 to +1)</b>",
        template="plotly_dark",
    )
    fig.update_traces(
        texttemplate="%{text:+.2f}",
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>Net Sentiment: %{x:+.2f}<extra></extra>",
    )
    fig.update_layout(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=50, b=30, l=40, r=30),
        font=dict(family="Inter, sans-serif"),
        coloraxis_showscale=False,
    )
    fig.update_xaxes(range=[-1.15, 1.15], zeroline=True, zerolinecolor="rgba(255,255,255,0.3)")
    return fig


