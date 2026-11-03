"""BehaviorIQ dashboard (Stage 11).    streamlit run dashboard/app.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from biq import config  # noqa: E402
from biq.warehouse import connect  # noqa: E402

st.set_page_config(page_title="BehaviorIQ", page_icon="◐", layout="wide")
COL = {"purchase": "#0F8B8D", "abandon": "#B42318", "browse": "#8A94A6"}
INK = "#1E2A3A"
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, .stText { font-family: 'IBM Plex Sans', sans-serif; color: #1E2A3A; }
h1 { font-weight: 600; letter-spacing: -0.02em; }
h2, h3 { font-weight: 500; }
.trail { display:flex; flex-wrap:wrap; gap:4px; margin:6px 0 14px; }
.tok { padding:3px 8px; border-radius:3px; font-size:12.5px; background:#EEF1F5; color:#1E2A3A; border:1px solid #D9DEE6; }
.tok.seen { background:#1E2A3A; color:#fff; border-color:#1E2A3A; }
.tok.term { outline:2px dashed #8A94A6; }
.bar { height:10px; border-radius:2px; }
</style>""", unsafe_allow_html=True)


@st.cache_data
def q(sql: str) -> pd.DataFrame:
    con = connect(read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


@st.cache_data
def sessions() -> pd.DataFrame:
    return pd.read_parquet(config.DATA / "sessions_split.parquet")


def report(name: str) -> str:
    p = config.REPORTS / name
    return p.read_text(encoding="utf-8") if p.exists() else f"_Run the pipeline to create {name}._"


st.title("BehaviorIQ")
st.caption("Real-time behavioural intelligence over clickstream sessions")

tabs = st.tabs(["Overview", "Session journey", "Segments", "Research results", "Ask BehaviorIQ", "Monitoring"])

# ------------------------------------------------------------------ overview
with tabs[0]:
    o = q("SELECT * FROM v_outcome_summary")
    total = int(o.session_count.sum())
    c = st.columns(4)
    c[0].metric("Sessions", f"{total:,}")
    for col, name in zip(c[1:], ["purchase", "abandon", "browse"]):
        n = int(o.set_index("outcome").session_count.get(name, 0))
        col.metric(f"{name.capitalize()}", f"{100 * n / total:.1f}%", f"{n:,} sessions", delta_color="off")
    f = q("SELECT SUM(session_count) sessions, SUM(reached_product) product, SUM(reached_cart) cart, "
          "SUM(reached_checkout) checkout, SUM(purchased) purchase FROM v_funnel_daily").T.reset_index()
    f.columns = ["stage", "sessions"]
    left, right = st.columns([3, 2])
    fig = px.funnel(f, x="sessions", y="stage", color_discrete_sequence=[INK])
    fig.update_layout(title="Where sessions drop out", margin=dict(l=0, r=0, t=40, b=0), height=330)
    left.plotly_chart(fig, width="stretch")
    ca = q("SELECT * FROM v_compare_abandonment")
    ca["compares"] = ca.compare_events_capped.astype(str).replace("4", "4+")
    fig = px.bar(ca, x="compares", y="abandonment_rate", color_discrete_sequence=[COL["abandon"]],
                 hover_data=["session_count"])
    fig.update_layout(title="More comparing, more abandoning", yaxis_tickformat=".0%", height=330,
                      margin=dict(l=0, r=0, t=40, b=0), xaxis_title="compare events in session", yaxis_title="abandonment rate")
    right.plotly_chart(fig, width="stretch")
    d = q("SELECT day, conversion_rate, session_count FROM v_funnel_daily")
    fig = px.line(d, x="day", y="conversion_rate", markers=True, color_discrete_sequence=[COL["purchase"]])
    fig.update_layout(title="Daily conversion", yaxis_tickformat=".0%", height=260, margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ session journey
with tabs[1]:
    from biq import serving
    s = sessions()
    test = s[(s.split == "test") & (s.n_events >= 6)]
    st.write("Pick a held-out session and slide through it. Intent is re-predicted from only the events seen so far, "
             "using its embedding plus the outcomes of the 10 most similar earlier sessions.")
    a, b = st.columns([2, 1])
    choice = a.selectbox("Session", test.session_id.head(300).tolist(),
                         format_func=lambda x: f"{x}  ({int(test.set_index('session_id').n_events[x])} events)")
    row = test.set_index("session_id").loc[choice]
    from biq.schema import prediction_prefix
    usable = prediction_prefix(list(row.events))
    k = b.slider("Events observed", 1, usable, min(3, usable))
    ev = list(row.events)
    chips = "".join(f'<span class="tok {"seen" if i < k else ""} {"term" if e in ("purchase", "exit") else ""}">{e}</span>'
                    for i, e in enumerate(ev))
    st.markdown(f'<div class="trail">{chips}</div>', unsafe_allow_html=True)
    p = serving.predict_session(choice, k)
    dec = serving.decide(p["probabilities"], p["device"], seed=0)
    cols = st.columns(3)
    for col, (name, v) in zip(cols, p["probabilities"].items()):
        col.markdown(f"**{name}** &nbsp; {100 * v:.0f}%")
        col.markdown(f'<div class="bar" style="width:{max(v, .02) * 100:.0f}%;background:{COL[name]}"></div>', unsafe_allow_html=True)
    st.write("")
    m1, m2, m3 = st.columns(3)
    m1.metric("Actual outcome", row.outcome)
    m2.metric("Next-best action", dec["action"].replace("_", " "))
    m3.metric("Similar earlier sessions", ", ".join(f"{n} {o}" for o, n in p["neighbour_outcomes"].items()))
    ex = serving.explain(p["X"], n_events=None if k >= usable else k)
    fig = px.bar(ex.reset_index(), x="shap", y="index", orientation="h",
                 color=np.where(ex.shap > 0, "pushes towards", "pushes away"),
                 color_discrete_map={"pushes towards": COL[ex.attrs['explained_class']], "pushes away": "#B8C0CC"})
    fig.update_layout(title=f"Why '{ex.attrs['explained_class']}'? (SHAP)", yaxis_title="", xaxis_title="contribution",
                      height=320, margin=dict(l=0, r=0, t=40, b=0), legend_title="", yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch")

# ------------------------------------------------------------------ segments
with tabs[2]:
    seg = pd.read_parquet(config.DATA / "segments.parquet")
    xy = np.load(config.DATA / "cluster_xy.npy")
    ss = sessions().merge(seg, on="session_id")
    base = pd.read_parquet(config.SESSIONS)[["session_id"]].merge(seg, on="session_id")
    base["x"], base["y"] = xy[:, 0], xy[:, 1]
    base = base.merge(sessions()[["session_id", "outcome"]], on="session_id")
    sample = base.sample(min(3000, len(base)), random_state=0)
    left, right = st.columns([3, 2])
    fig = px.scatter(sample, x="x", y="y", color="segment", opacity=.55, hover_data=["outcome"])
    fig.update_traces(marker=dict(size=5))
    fig.update_layout(title="Behavioural personas found by HDBSCAN (2-D projection)", height=460,
                      margin=dict(l=0, r=0, t=40, b=0), xaxis_title="", yaxis_title="")
    left.plotly_chart(fig, width="stretch")
    summ = q("SELECT segment, session_count, conversion_rate, abandonment_rate, avg_events FROM v_segment_summary ORDER BY session_count DESC")
    right.dataframe(summ.style.format({"conversion_rate": "{:.1%}", "abandonment_rate": "{:.1%}", "avg_events": "{:.1f}"}),
                    hide_index=True, width="stretch", height=300)
    with st.expander("Mining report: sequential patterns and association rules"):
        st.markdown(report("stage5_mining.md"))

# ------------------------------------------------------------------ research
with tabs[3]:
    st.markdown(report("stage8/intent.md"))
    c1, c2 = st.columns(2)
    for col, img in ((c1, "early_curve.png"), (c2, "reliability.png")):
        p = config.REPORTS / "stage8" / img
        if p.exists():
            col.image(str(p))
    st.markdown(report("stage8/bandit.md"))
    c1, c2 = st.columns(2)
    for col, img in ((c1, "bandit_reward.png"), (c2, "bandit_actions.png")):
        p = config.REPORTS / "stage8" / img
        if p.exists():
            col.image(str(p))
    with st.expander("Representation and retrieval checks"):
        st.markdown(report("stage6_encoder.md"))
        st.markdown(report("stage7_retrieval.md"))

# ------------------------------------------------------------------ copilot
with tabs[4]:
    import os
    from biq.copilot import ask
    mode = f"Claude ({config.LLM_MODEL})" if os.getenv("ANTHROPIC_API_KEY") else "offline mode (set ANTHROPIC_API_KEY for open questions)"
    st.caption(f"Answers come only from the warehouse and the vector index. Mode: {mode}. "
               f"Figures from fewer than {config.K_ANON_MIN} sessions are withheld.")
    examples = ["Which behavioral segment has the highest abandonment rate?", "What is the conversion rate on mobile?",
                "How does comparing products relate to abandonment?",
                "What do sessions similar to users who compared multiple products usually end in?"]
    ex = st.selectbox("Try a question", [""] + examples)
    question = st.text_input("Ask a question about user behaviour", value=ex)
    if st.button("Ask", type="primary") and question:
        with st.spinner("Querying the warehouse"):
            r = ask(question)
        st.markdown(f"#### {r['answer']}")
        st.caption("Every number above was checked against the query results." if r["grounded"] else
                   "Some numbers could not be verified against the data.")
        with st.expander("Show the queries and rows used"):
            for sql in r["sql"]:
                st.code(sql, language="sql")
            st.json(r["tool_outputs"])

# ------------------------------------------------------------------ monitoring
with tabs[5]:
    st.markdown(report("stage10_drift.md"))
    det = config.REPORTS / "stage10_drift_detail.csv"
    if det.exists():
        d = pd.read_csv(det)
        fig = px.bar(d, x="feature", y="psi", color="scenario", barmode="group")
        fig.add_hline(y=0.2, line_dash="dash", line_color=COL["abandon"], annotation_text="PSI 0.2 drift threshold")
        fig.update_layout(height=380, title="Population stability per feature and scenario", margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig, width="stretch")
    st.markdown(report("stage9_copilot.md"))
    st.caption("Experiment tracking and the model registry: run `mlflow ui --backend-store-uri sqlite:///mlflow.db`.")
