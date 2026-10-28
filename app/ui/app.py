"""Streamlit UI – Brand Review Intelligence Dashboard.

Features:
 - Single Brand Deep Dive OR Multi-brand Competitive Benchmark.
 - Scale Presets: Quick Test (~75), Standard (~250), Large (~1,000), Massive (~3,000+).
 - Direct Google Maps URLs input to bypass discovery and scrape reviews at 5x speed.
 - Configurable polling window so large review scrapes don't timeout.
 - Cloud Snapshot Sync button: instantly pulls completed Bright Data reviews.
 - Interactive charts: Rating Distribution, Monthly Sentiment Trajectory, Aspect Breakdown, Share of Voice.
 - Searchable and filterable Review Explorer.
 - 1-Click Excel Reports (Study Insights Workbook, Single-brand KPI Workbook, Clean CSV).
"""
import os
import time
import requests
import pandas as pd
import streamlit as st

try:
    from components import (
        inject_premium_css,
        plot_sentiment_trend,
        plot_share_of_voice,
        plot_rating_distribution,
        plot_aspect_breakdown,
    )
except ImportError:
    from app.ui.components import (
        inject_premium_css,
        plot_sentiment_trend,
        plot_share_of_voice,
        plot_rating_distribution,
        plot_aspect_breakdown,
    )

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1").rstrip("/")
REQUEST_TIMEOUT = 60


# ── helpers ───────────────────────────────────────────────────────────────────

def api(method: str, path: str, **kw):
    kw.setdefault("timeout", REQUEST_TIMEOUT)
    try:
        return requests.request(method, f"{API_BASE}{path}", **kw)
    except (requests.ConnectionError, requests.Timeout):
        return None


def ok(r) -> bool:
    return r is not None and r.ok


def detail(r) -> str:
    if r is None:
        return "Cannot reach backend – is `uvicorn app.main:app --reload` running on port 8000?"
    try:
        d = r.json()
        return d.get("detail") or str(d)[:400]
    except Exception:
        return (r.text or "")[:400]


def status_icon(status: str) -> str:
    return {
        "completed": "🟢",
        "completed_with_warnings": "🟡",
        "running": "🔵",
        "queued": "🔵",
        "interrupted": "🟠",
        "failed": "🔴",
        "error": "🔴",
    }.get(status, "⚪")


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Brand Review Intelligence",
    layout="wide",
    initial_sidebar_state="collapsed",
    page_icon="🍔",
)
inject_premium_css()
st.title("🍔 Brand Review Intelligence")

# ── backend health check ──────────────────────────────────────────────────────

_health = api("GET", "/health")
if not ok(_health):
    st.error(
        "🚨 **Backend unreachable.** Please start the API server with: "
        "`uvicorn app.main:app --reload` in your terminal.",
        icon="⚠️",
    )
    st.stop()

hdata = _health.json()
is_mock = "MOCK" in hdata.get("mode", "")
mode_badge = "🟡 MOCK Mode (Synthetic Fast Data)" if is_mock else "🟢 LIVE Mode (Bright Data Scraper API)"

h_col1, h_col2 = st.columns([4, 1])
with h_col1:
    st.caption(
        f"Backend: **{mode_badge}** | API key configured: `{hdata.get('api_key_set')}` | "
        f"API Base: `{API_BASE}`"
    )
with h_col2:
    if st.button("🔄 Reload App", use_container_width=True):
        st.rerun()

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_launch, tab_monitor, tab_insights = st.tabs([
    "🚀 Launch Review Collection",
    "📡 Active Runs & Monitor",
    "📊 Insights & Export Center",
])


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: LAUNCH REVIEW COLLECTION
# ═══════════════════════════════════════════════════════════════════════════════

with tab_launch:
    st.header("Collect Brand Customer Reviews")
    st.caption(
        "Gather high-volume customer reviews from Google Maps for Burger King, Subway, "
        "McDonald's, Wendy's, or any brand. Choose a single brand deep collection or a competitive study."
    )

    _src_r = api("GET", "/sources")
    sources_available = (
        [s["name"] for s in _src_r.json()] if ok(_src_r) else ["google_maps"]
    )

    _brands_r = api("GET", "/brands")
    known_brands = (
        _brands_r.json()
        if ok(_brands_r) and isinstance(_brands_r.json(), list)
        else ["Burger King", "Subway", "McDonald's", "Wendy's"]
    )
    # Ensure Burger King is always prominently available
    for b in ["Burger King", "Subway", "McDonald's", "Wendy's"]:
        if b not in known_brands:
            known_brands.append(b)

    # Mode Selector
    mode = st.radio(
        "**Collection Mode**",
        options=["🎯 Single Brand Deep Dive", "⚔️ Competitive Benchmark Study"],
        horizontal=True,
        help="Single Brand gives 100% of the collection budget to your chosen brand (e.g. Burger King).",
    )
    is_single_brand = mode.startswith("🎯")

    with st.form("new_collection_form"):
        f1, f2 = st.columns(2)

        with f1:
            study_name = st.text_input(
                "Study / Project Name",
                value="Burger King Deep Dive" if is_single_brand else "QSR Benchmark 2026",
            )
            default_brand_idx = known_brands.index("Burger King") if "Burger King" in known_brands else 0
            focus_brand = st.selectbox(
                "Target Brand" if is_single_brand else "Focus Brand",
                options=known_brands,
                index=default_brand_idx,
                help="The primary restaurant brand to collect reviews for.",
            )

        with f2:
            if not is_single_brand:
                available_comps = [b for b in known_brands if b != focus_brand]
                default_comps = [b for b in available_comps if b in ["Subway", "McDonald's", "Wendy's"]]
                competitors = st.multiselect(
                    "Competitor Brands",
                    options=available_comps,
                    default=default_comps,
                    help="Select competitor brands to compare against.",
                )
            else:
                competitors = []
                st.info(
                    f"**Collecting exclusively for `{focus_brand}`.**  \n"
                    "All budget and store limits will be dedicated to gathering max reviews for this brand.",
                    icon="🎯",
                )

        st.markdown("---")
        st.subheader("📦 Dataset Volume & Speed Presets")

        preset = st.radio(
            "Select Scale Preset",
            options=[
                "⚡ Quick Test (~75 reviews, ~1-2 min)",
                "📊 Standard Sample (~250 reviews, ~3-4 min)",
                "🏢 Large Dataset (~1,000 reviews, ~5-8 min)",
                "🚀 Massive Deep Dive (~3,000+ reviews, ~8-12 min)",
                "🛠️ Custom Configuration",
            ],
            index=2 if not is_single_brand else 2,  # Default to Large Dataset
            horizontal=False,
        )

        # Set preset defaults
        if "Quick Test" in preset:
            p_places, p_revs, p_days, p_budget = 3, 25, 30, 500
        elif "Standard Sample" in preset:
            p_places, p_revs, p_days, p_budget = 5, 50, 90, 1500
        elif "Large Dataset" in preset:
            p_places, p_revs, p_days, p_budget = 10, 100, 365, 5000
        elif "Massive Deep Dive" in preset:
            p_places, p_revs, p_days, p_budget = 15, 200, 730, 8000
        else:
            p_places, p_revs, p_days, p_budget = 5, 50, 90, 3000

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            max_places = st.number_input(
                "Stores to Scrape",
                value=p_places,
                min_value=1,
                max_value=50,
                step=1,
                help="How many locations to collect reviews from.",
            )
        with c2:
            max_reviews_per_place = st.number_input(
                "Reviews per Store",
                value=p_revs,
                min_value=5,
                max_value=1000,
                step=10,
                help="Max reviews to pull per store.",
            )
        with c3:
            days_limit = st.number_input(
                "Time Window (Days)",
                value=p_days,
                min_value=1,
                max_value=3650,
                step=30,
                help="Reviews newer than this many days (e.g. 365 = past 1 year).",
            )
        with c4:
            budget = st.number_input(
                "Budget Cap (Max Total Records)",
                value=p_budget,
                min_value=100,
                max_value=10000,
                step=500,
                help="Hard cap on total reviews collected across all stores.",
            )

        st.markdown("---")
        with st.expander("⚡ Speed Boost: Direct Google Maps Store URLs (Skip 2–4 min Discovery)", expanded=False):
            st.caption(
                "💡 **Pro-Tip**: Pasting direct Google Maps place URLs skips the 2–4 minute city search "
                "and starts scraping reviews immediately!"
            )
            direct_urls_text = st.text_area(
                "Google Maps Store URLs (one per line, optional)",
                placeholder=(
                    "https://www.google.com/maps/place/Burger+King/@40.7589,-73.9851,17z/\n"
                    "https://www.google.com/maps/place/Burger+King/@34.0522,-118.2437,17z/"
                ),
                height=100,
            )

        sources = st.multiselect(
            "Sources to Pull From",
            options=sources_available,
            default=["google_maps"],
        )

        submitted = st.form_submit_button(
            "🚀 Launch Collection Pipeline",
            use_container_width=True,
            type="primary",
        )

    if submitted:
        if not sources:
            st.error("Please select at least one data source.")
        elif not focus_brand:
            st.error("Target Brand is required.")
        else:
            direct_urls = [u.strip() for u in (direct_urls_text or "").splitlines() if u.strip().startswith("http")]
            payload = {
                "name": study_name,
                "focus_brand": focus_brand,
                "competitors": competitors,
                "sources": sources,
                "days_limit": int(days_limit),
                "max_places": int(max_places) if not direct_urls else len(direct_urls),
                "max_reviews_per_place": int(max_reviews_per_place),
                "study_budget": int(budget),
                "google_maps_urls": direct_urls,
            }
            with st.spinner("Queueing collection pipeline with backend..."):
                r = api("POST", "/studies", json=payload)
            if ok(r):
                data = r.json()
                st.success(
                    f"✅ **Success!** Study **`{data['study_id']}`** launched — "
                    f"{len(data['run_ids'])} brand run(s) queued. "
                    "Switching to **Active Runs & Monitor** tab."
                )
                time.sleep(1.2)
                st.rerun()
            else:
                st.error(f"Launch failed ({r.status_code if r else 'no response'}): {detail(r)}")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: ACTIVE RUNS & MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

with tab_monitor:
    st.header("Active Collections & Pipeline Monitor")

    r_studies = api("GET", "/studies")
    if not ok(r_studies):
        st.error(detail(r_studies))
        st.stop()

    studies = r_studies.json()
    if not studies:
        st.info("No studies found. Launch a collection in the **Launch Review Collection** tab.")
        st.stop()

    study_map = {f"{s['name']}  ({s['id']}) - {s.get('focus_brand', '')}": s["id"] for s in studies}
    top_c1, top_c2 = st.columns([4, 1])
    with top_c1:
        selected_study_label = st.selectbox("Select Study / Project", options=list(study_map.keys()))
        s_id = study_map[selected_study_label]
    with top_c2:
        auto_refresh = st.toggle("⟳ Auto-refresh (10s)", value=True)

    r_study = api("GET", f"/studies/{s_id}")
    if not ok(r_study):
        st.error(detail(r_study))
        st.stop()

    study_data = r_study.json()
    runs = study_data.get("runs") or []

    any_active = any(r.get("status") in {"running", "queued"} for r in runs)

    # Action Toolbar
    act_c1, act_c2, act_c3 = st.columns([3, 2, 1])
    with act_c1:
        if any_active:
            st.info(
                "⏳ **Scraping in progress:** Bright Data is harvesting reviews. "
                "Embedded store ratings appear first; the full review dataset completes shortly.",
                icon="📡",
            )
        else:
            st.success("✅ **Data collection completed for this study.**", icon="✨")

    with act_c2:
        # Cloud Sync button: Pull completed Bright Data snapshot
        if st.button("⚡ Sync Cloud Snapshot (Fetch Completed Reviews)", use_container_width=True,
                     help="Checks Bright Data cloud servers immediately. If the scraping snapshot has finished, downloads all reviews now."):
            sync_resp = api("POST", f"/studies/{s_id}/sync-cloud")
            if ok(sync_resp):
                st.success("Cloud sync triggered! Polling cloud snapshot...")
                time.sleep(1.0)
                st.rerun()
            else:
                st.warning(f"Sync cloud notice: {detail(sync_resp)}")

    with act_c3:
        if st.button("🔄 Refresh Now", use_container_width=True):
            st.rerun()

    # Per-brand metrics
    st.markdown("### Brand Runs")
    run_cols = st.columns(max(len(runs), 1))
    for i, run in enumerate(runs):
        run_id = run["id"]
        status = run.get("status", "unknown")
        brand = run.get("brand", "Unknown")

        detail_r = api("GET", f"/runs/{run_id}")
        rdata = detail_r.json() if ok(detail_r) else {}
        review_count = sum(s.get("reviews", 0) for s in rdata.get("by_source", []))

        with run_cols[i]:
            with st.container(border=True):
                st.markdown(f"#### {status_icon(status)} {brand}")
                st.caption(f"Status: **{status.replace('_', ' ').title()}**")
                st.metric("Reviews Stored", f"{review_count:,}")
                stage_text = run.get("stage") or "idle"
                st.caption(f"Stage: `{stage_text}`")

                # Action buttons
                b_c1, b_c2 = st.columns(2)
                with b_c1:
                    if status in {"interrupted", "failed", "completed_with_warnings"}:
                        if st.button("▶ Resume", key=f"res_{run_id}", use_container_width=True):
                            r_res = api("POST", f"/runs/{run_id}/resume")
                            if ok(r_res):
                                st.success("Resuming...")
                                time.sleep(0.8)
                                st.rerun()
                with b_c2:
                    if st.button("⚡ Sync", key=f"syn_{run_id}", use_container_width=True, help="Fetch ready cloud snapshot"):
                        r_syn = api("POST", f"/runs/{run_id}/resume", params={"sync_cloud": "true"})
                        if ok(r_syn):
                            st.success("Syncing cloud snapshot...")
                            time.sleep(0.8)
                            st.rerun()

                # Analysis trigger
                an_r = api("GET", f"/runs/{run_id}/analysis")
                an_data = an_r.json() if ok(an_r) else {}
                an_status = an_data.get("status", "not_started")
                if an_status == "running":
                    st.progress(
                        (an_data.get("processed", 0) / max(an_data.get("total", 1), 1)),
                        text=f"Analysing {an_data.get('processed', 0)}/{an_data.get('total', 0)}...",
                    )
                elif an_status == "completed":
                    st.caption("🧠 Analysis: Completed")
                elif review_count > 0:
                    if st.button("🔬 Analyze Menu Aspects", key=f"an_btn_{run_id}", use_container_width=True):
                        api("POST", f"/runs/{run_id}/analyze", json={"method": "lexicon"})
                        st.info("Analysis queued.")
                        time.sleep(0.5)
                        st.rerun()

    # Per-run Detail Expanders (Jobs, Logs, Preview)
    st.markdown("---")
    st.markdown("### Run Diagnostics & Live Feed")
    for run in runs:
        run_id = run["id"]
        status = run.get("status", "unknown")
        brand = run.get("brand", "Unknown")

        with st.expander(f"{status_icon(status)} **{brand}** — Detailed Jobs, Event Logs & Sample", expanded=False):
            d_r = api("GET", f"/runs/{run_id}")
            if not ok(d_r):
                st.warning("Could not load details.")
                continue
            rdata = d_r.json()

            if rdata.get("error"):
                st.warning(f"⚠️ Notice: {rdata['error']}")

            # Jobs Table
            jobs = rdata.get("jobs") or []
            if jobs:
                st.markdown("**Scraper Jobs**")
                j_df = pd.DataFrame([
                    {
                        "Source": j["source"],
                        "Kind": j["kind"],
                        "Status": j["status"],
                        "Records": j.get("records", 0),
                        "Errors": j.get("error_records", 0),
                        "Snapshot ID": j.get("snapshot_id") or "None",
                        "Message": j.get("message") or "",
                    }
                    for j in jobs
                ])
                st.dataframe(j_df, use_container_width=True, hide_index=True)

            # Event Logs
            evs = rdata.get("events") or []
            if evs:
                st.markdown("**Live Events Feed (Latest First)**")
                for ev in reversed(evs[:10]):
                    st.code(f"[{ev['ts']}] {ev['message']}", language=None)

            # Review Preview
            revs_prev = api("GET", f"/runs/{run_id}/reviews", params={"limit": 8})
            if ok(revs_prev) and revs_prev.json().get("results"):
                st.markdown(f"**Latest Reviews Preview ({brand})**")
                p_df = pd.DataFrame([
                    {
                        "Date": r.get("review_date", ""),
                        "Rating": f"{r.get('rating', '')} ⭐",
                        "Store": (r.get("place_name") or brand)[:30],
                        "Review": (r.get("text") or "")[:200],
                    }
                    for r in revs_prev.json()["results"]
                ])
                st.dataframe(p_df, use_container_width=True, hide_index=True)

    # Auto-refresh trigger
    if auto_refresh and any_active:
        time.sleep(10)
        st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: INSIGHTS & EXPORT CENTER
# ═══════════════════════════════════════════════════════════════════════════════

with tab_insights:
    st.header("Brand Analytics & Data Export")

    if not studies:
        st.info("No studies available yet.")
        st.stop()

    ins_s_label = st.selectbox(
        "Select Study to Analyze & Export",
        options=list(study_map.keys()),
        key="ins_study_select",
    )
    ins_s_id = study_map[ins_s_label]

    r_ins = api("GET", f"/studies/{ins_s_id}/insights")
    ins_data = r_ins.json() if ok(r_ins) else {}
    brands_ins = ins_data.get("brands") or []

    # Get active runs in study
    r_study_info = api("GET", f"/studies/{ins_s_id}")
    curr_runs = (r_study_info.json() if ok(r_study_info) else {}).get("runs", [])

    # Fetch all reviews across runs for distribution & table
    study_all_revs = []
    for r in curr_runs:
        r_revs = api("GET", f"/runs/{r['id']}/reviews", params={"limit": 1000})
        if ok(r_revs):
            study_all_revs.extend(r_revs.json().get("results", []))

    # Trigger analysis automatically if needed
    for r in curr_runs:
        r_id = r["id"]
        an_r = api("GET", f"/runs/{r_id}/analysis")
        an_st = (an_r.json() if ok(an_r) else {}).get("status", "not_started")
        if an_st == "not_started":
            api("POST", f"/runs/{r_id}/analyze", json={"method": "lexicon"})

    # ── Executive KPI Scorecards ──────────────────────────────────────────────
    st.markdown("### Executive Performance Summary")
    if brands_ins:
        for b in brands_ins:
            b_name = b["brand"]
            is_focus = b_name == ins_data.get("focus_brand")

            with st.container(border=True):
                st.markdown(f"### {b_name} {'⭐ Focus Brand' if is_focus else ''}")
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Total Reviews", f"{b.get('reviews', 0):,}")
                m2.metric("Menu Mentions", f"{b.get('mentions', 0):,}")
                m3.metric("Average Rating", f"{b.get('mean_rating', 0):.2f} / 5.0" if b.get('mean_rating') else "—")
                m4.metric("Net Sentiment", f"{b.get('mean_sentiment', 0):+.2f}")
                m5.metric("Negative Share", f"{b.get('negative_share', 0)*100:.1f}%")

                if not is_focus and "delta_vs_focus" in b:
                    delta = b["delta_vs_focus"]
                    s_del = delta.get("sentiment")
                    r_del = delta.get("rating")
                    st.caption(
                        f"**Benchmark vs {ins_data.get('focus_brand')}:** "
                        f"Sentiment Δ: **{s_del:+.2f}** | Rating Δ: **{r_del:+.2f}**"
                    )

    # ── Visualizations ────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### Interactive Visualizations")

    chart_c1, chart_c2 = st.columns(2)
    with chart_c1:
        if study_all_revs:
            r_chart = plot_rating_distribution(study_all_revs)
            if r_chart:
                st.plotly_chart(r_chart, use_container_width=True)
        else:
            st.info("Collecting reviews to generate star rating distribution...")

    with chart_c2:
        if brands_ins:
            if len(brands_ins) > 1:
                sov_chart = plot_share_of_voice(brands_ins)
                if sov_chart:
                    st.plotly_chart(sov_chart, use_container_width=True)
            else:
                trend_chart = plot_sentiment_trend(brands_ins)
                if trend_chart:
                    st.plotly_chart(trend_chart, use_container_width=True)
                else:
                    st.info("Monthly trend will plot once reviews are dated.")

    # Aspect Breakdown
    all_study_ments = []
    for r in curr_runs:
        m_resp = api("GET", f"/runs/{r['id']}/insights/items")
        if ok(m_resp):
            all_study_ments.extend(m_resp.json())

    # ── Searchable Review Explorer ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 🔍 Customer Review Explorer")

    if study_all_revs:
        f_col1, f_col2, f_col3 = st.columns([2, 1, 1])
        with f_col1:
            search_query = st.text_input("Search Review Text", placeholder="e.g. whopper, fries, cold, slow, staff")
        with f_col2:
            rating_filter = st.selectbox("Filter Star Rating", options=["All Stars", "5 Stars", "4 Stars", "3 Stars", "2 Stars", "1 Star"])
        with f_col3:
            brand_filter = st.selectbox("Filter Brand", options=["All Brands"] + list({r.get("brand") for r in study_all_revs if r.get("brand")}))

        filtered = study_all_revs
        if search_query:
            filtered = [r for r in filtered if search_query.lower() in (r.get("text") or "").lower()]
        if rating_filter != "All Stars":
            target_star = int(rating_filter[0])
            filtered = [r for r in filtered if r.get("rating") == target_star]
        if brand_filter != "All Brands":
            filtered = [r for r in filtered if r.get("brand") == brand_filter]

        st.caption(f"Showing **{len(filtered):,}** matching reviews (sorted newest first):")
        rev_table = pd.DataFrame([
            {
                "Brand": r.get("brand", ""),
                "Date": r.get("review_date", ""),
                "Stars": f"{r.get('rating', '')} ⭐",
                "Store Name": r.get("place_name", ""),
                "Review Text": r.get("text", ""),
                "Address": r.get("address", ""),
            }
            for r in filtered[:100]
        ])
        st.dataframe(rev_table, use_container_width=True, hide_index=True)
    else:
        st.info("No reviews available yet for this study.")

    # ── One-Click Downloads Center ────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📥 Export Reports & Raw Data")
    st.caption("Download formula-driven Excel spreadsheets with charts, or raw CSV datasets.")

    ex_c1, ex_c2 = st.columns(2)

    with ex_c1:
        st.markdown("**Comprehensive Multi-Sheet Excel Report**")
        st.caption("Includes: Reviews (Raw), Places (Raw), Comparative Metrics, Aspect Deltas, Monthly Trends.")
        study_exp = api("GET", f"/studies/{ins_s_id}/export.xlsx", timeout=90)
        if ok(study_exp):
            safe_name = (ins_s_label.split(" (")[0]).replace(" ", "_").replace("'", "")
            st.download_button(
                "⬇️ Download Full Study Excel (.xlsx)",
                data=study_exp.content,
                file_name=f"{safe_name}_full_insights.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                type="primary",
            )
        else:
            st.info("Full study report compiling... available once data is loaded.")

    with ex_c2:
        st.markdown("**Per-Brand Intelligence Spreadsheets & CSV**")
        for r in curr_runs:
            b_name = r.get("brand", "brand")
            b_clean = b_name.lower().replace(" ", "_").replace("'", "")

            sub_c1, sub_c2 = st.columns(2)
            with sub_c1:
                # Single brand Excel with formula KPI summary & chart
                b_xlsx = api("GET", f"/runs/{r['id']}/export.xlsx", timeout=60)
                if ok(b_xlsx):
                    st.download_button(
                        f"📊 {b_name} Excel Sheet",
                        data=b_xlsx.content,
                        file_name=f"{b_clean}_kpi_report.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                        key=f"xlsx_btn_{r['id']}",
                    )
            with sub_c2:
                # Clean CSV
                b_csv = api("GET", f"/runs/{r['id']}/export.csv", timeout=30)
                if ok(b_csv):
                    st.download_button(
                        f"📄 {b_name} CSV",
                        data=b_csv.content,
                        file_name=f"{b_clean}_reviews.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key=f"csv_btn_{r['id']}",
                    )
