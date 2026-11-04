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
import streamlit.components.v1 as components  # noqa: E402

from biq import config  # noqa: E402
from biq.warehouse import connect  # noqa: E402

st.set_page_config(page_title="BehaviorIQ", page_icon="◐", layout="wide")
COL = {"purchase": "#0F8B8D", "abandon": "#B42318", "browse": "#8A94A6"}
INK = "#1E2A3A"
st.markdown("""
<style>
:root { color-scheme: light; }
.stApp, [data-testid="stAppViewContainer"] { background: #F3F6FB; }
.stApp, .stApp p, .stApp label, [data-testid="stMarkdownContainer"] {
    font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
    color: #17263B;
}
.stApp h1, .stApp h2, .stApp h3, .stApp h4 {
    color: #14243A;
    font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
    font-weight: 650;
    letter-spacing: -0.025em;
}
.stApp h1 { font-size: 2.25rem; }
[data-testid="stCaptionContainer"] { color: #52647B; }
[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #DCE5F0;
    border-radius: 12px;
    padding: 14px 16px;
    box-shadow: 0 2px 8px rgba(27, 49, 79, 0.045);
}
[data-testid="stMetricLabel"] p { color: #52647B; font-weight: 600; }
[data-testid="stMetricValue"] { color: #14243A; font-weight: 650; }
[data-baseweb="tab-list"] {
    gap: 6px;
    background: #E8EEF6;
    border-radius: 11px;
    padding: 5px;
}
button[role="tab"] {
    background: transparent;
    border-radius: 8px;
    color: #435670;
    font-weight: 600;
}
button[role="tab"][aria-selected="true"] {
    background: #FFFFFF;
    color: #234B83;
    box-shadow: 0 1px 4px rgba(27, 49, 79, 0.12);
}
.stApp input, .stApp textarea, .stApp [data-baseweb="select"] > div {
    background: #FFFFFF;
    color: #17263B;
    border-color: #CBD7E6;
}
.stApp input::placeholder, .stApp textarea::placeholder { color: #65768C; }
.stButton > button[kind="primary"] {
    background: #315B91;
    border-color: #315B91;
    color: #FFFFFF;
    font-weight: 600;
}
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
    segment_options = sorted(base.segment.dropna().unique().tolist())
    selected_segment = st.selectbox("Filter by behavioral segment", ["All segments", *segment_options])
    if selected_segment != "All segments":
        base = base[base.segment == selected_segment]
    sample = base.sample(min(3000, len(base)), random_state=0)
    left, right = st.columns([3, 2])
    fig = px.scatter(sample, x="x", y="y", color="segment", opacity=.55, hover_data=["outcome"])
    fig.update_traces(marker=dict(size=5))
    fig.update_layout(title="Behavioural personas found by HDBSCAN (2-D projection)", height=460,
                      margin=dict(l=0, r=0, t=40, b=0), xaxis_title="", yaxis_title="")
    left.plotly_chart(fig, width="stretch")
    summ = q("SELECT segment, session_count, conversion_rate, abandonment_rate, avg_events FROM v_segment_summary ORDER BY session_count DESC")
    if selected_segment != "All segments":
        summ = summ[summ.segment == selected_segment]
    right.dataframe(summ.style.format({"conversion_rate": "{:.1%}", "abandonment_rate": "{:.1%}", "avg_events": "{:.1f}"}),
                    hide_index=True, width="stretch", height=300)
    with st.expander("Mining report: sequential patterns and association rules"):
        st.markdown(report("stage5_mining.md"))

# ------------------------------------------------------------------ research
with tabs[3]:
    st.header("Behaviour Representation")
    st.caption("Stage 6 — session representation evaluation")
    st.markdown(
        "**What are we testing?** Whether an embedding places sessions with the same eventual outcome "
        "near one another. The custom contrastive Transformer represents event sequences; the baseline "
        "uses TF-IDF n-grams with SVD, and the random reference reflects outcome frequencies.\n\n"
        "**Why does it matter?** A useful representation can help later retrieval and prediction compare "
        "behavioural patterns, but this agreement score is only a sanity check.\n\n"
        "**How was it tested?** The custom contrastive Transformer encodes event sequences and time gaps, "
        "pretraining it to bring augmented views of a session together. The baseline represents event "
        "1–3-grams with TF-IDF and SVD. Neighbour outcome agreement @10 is measured on the test split: "
        "the share of each session's ten nearest test-set neighbours with the same outcome. The random "
        "reference is the chance agreement implied by the test-set outcome mix.\n\n"
        "**What did we find?** The baseline scored **0.606**, above the custom Transformer at **0.579**; "
        "the random reference was **0.346**.\n\n"
        "**What should we conclude?** The baseline currently wins on this metric. The result does not "
        "establish that it is better for every downstream task or under leakage-safe serving retrieval."
    )
    with st.expander("How to interpret neighbour outcome agreement @10"):
        st.markdown(
            "For each test session, this measures the fraction of its ten closest sessions whose outcome "
            "matches. It is not classification accuracy, and the encoder check compares test sessions "
            "with other test sessions (excluding the query itself). The random reference is the sum of "
            "squared test-outcome shares, not a trained model."
        )

    st.header("Similar Customer Discovery")
    st.caption("Stage 7 — retrieval evaluation")
    st.markdown(
        "**What are we testing?** Whether nearest-neighbour search retrieves earlier sessions with the "
        "same eventual outcome as a held-out test session.\n\n"
        "**Why does it matter?** Similar past sessions can provide context for downstream intent "
        "predictions. Finding some relevant neighbours and finding most of all relevant sessions are "
        "different goals.\n\n"
        "**How was it tested?** Relevance means matching outcome; the retrieval pool is the training "
        "split, with no-self/no-future filtering. The abandonment check averages the share of abandoning "
        "sessions among the ten neighbours for 50 abandoning test sessions.\n\n"
        "**What did we find?** The custom top-10 results have precision **0.631** and recall **0.004**; "
        "the baseline has the same precision and recall. MRR is **0.796** for custom and **0.789** for "
        "baseline, while NDCG@10 is **0.639** and **0.640**, respectively. Abandoning neighbours make "
        "up **0.51** of the neighbours for 50 abandoning test sessions, versus a **0.36** abandonment "
        "base rate in the training pool (a 15 percentage-point difference).\n\n"
        "**What should we conclude?** Precision and ranking metrics describe the top results; recall "
        "describes coverage of the much larger same-outcome pool. Thus MRR/NDCG can be relatively high "
        "while recall remains low. The abandonment share is an enrichment in this 50-session sample, "
        "not evidence that an intervention changes outcomes."
    )
    with st.expander("How to interpret the retrieval metrics"):
        st.markdown(
            "- **Precision@10:** fraction of the ten returned sessions with the same outcome as the query.\n"
            "- **Recall@10:** matching sessions in the top ten divided by all same-outcome sessions in "
            "the training pool. A large pool makes recall small even when several top results match; "
            "the displayed denominator is the full pool, not only sessions eligible at query time.\n"
            "- **MRR:** average reciprocal rank of the first matching-outcome neighbour; higher means a "
            "match tends to appear earlier.\n"
            "- **NDCG@10:** ranking score that rewards matching-outcome neighbours near the top, compared "
            "with an ideal ordering.\n"
            "- **p50 / p95 latency:** median and 95th-percentile time, in milliseconds, for the timed "
            "single-query retrieval calls in this evaluation; they are not a production service-level guarantee.\n"
            "- **Abandonment neighbour share:** average share of abandon outcomes in the ten neighbours "
            "for the 50 selected abandon test sessions. The pool base rate (0.36) is the comparison point."
        )
    with st.expander("Representation and retrieval checks"):
        st.markdown(report("stage6_encoder.md"))
        st.markdown(report("stage7_retrieval.md"))

    st.header("Customer Intent Prediction")
    st.caption("Stage 8a — held-out, time-based intent evaluation")
    st.markdown(
        "**What are we testing?** How different session representations and retrieval features predict "
        "the session outcome (purchase, abandon, or browse).\n\n"
        "**Why does it matter?** More reliable intent estimates could help teams understand likely "
        "session outcomes sooner. This experiment measures prediction quality, not business lift.\n\n"
        "**How was it tested?** The variants use the time-based train/validation/test split. A, B, "
        "B+R, C_base, C, and C+R use the same XGBoost classifier; the retrieval feature variants use "
        "earlier training sessions. C-ft instead fine-tunes the Transformer with its classification head.\n\n"
        "**What did we find?** On the full-prefix table, B has macro-F1 **0.866**; B+R has **0.864**. "
        "The custom embedding variants C and C+R have macro-F1 **0.759** and **0.769**. The current "
        "results therefore do not show the learned embedding variants outperforming the hand-crafted "
        "features on macro-F1.\n\n"
        "**What should we conclude?** Treat these as held-out results on this dataset and setup. They "
        "do not establish production performance, causation, or real-world commercial impact."
    )
    with st.expander("What each intent variant represents"):
        st.markdown(
            "- **A:** aggregate counts and session statistics, including device and funnel stage.\n"
            "- **B:** A plus hand-crafted event-sequence and transition features.\n"
            "- **B+R:** B plus features from the retrieved neighbours' outcomes and similarities.\n"
            "- **C_base:** a generic text embedding of the event sequence; this run uses the TF-IDF "
            "1–3-gram and SVD-64 fallback reported below.\n"
            "- **C:** the custom contrastively pretrained Transformer embedding of the event sequence "
            "and time gaps.\n"
            "- **C+R:** C plus the same type of retrieval-derived features; the default uses ten "
            "earlier neighbours.\n"
            "- **C+R (k=5/20/50):** the C+R feature set with the number of retrieved neighbours changed "
            "to 5, 20, or 50.\n"
            "- **C-ft (Transformer head):** the Transformer fine-tuned end-to-end for intent with its "
            "classification head; it is not the XGBoost classifier used for the other listed variants.\n\n"
            "A, B, B+R, C_base, C, and C+R have three seeds in the full-prefix comparison. The k-sweep "
            "and C-ft rows are single runs; their displayed **± 0** is a report-format placeholder for "
            "the unavailable single-run standard deviation, not evidence of zero variability."
        )
    with st.expander("How to read the intent metrics and statistical comparison"):
        st.markdown(
            "- **Accuracy:** fraction of sessions assigned the correct outcome.\n"
            "- **Macro-F1:** F1 is the balance of precision and recall; macro-F1 averages it equally "
            "across purchase, abandon, and browse, so each outcome has equal weight.\n"
            "- **ROC-AUC:** macro one-vs-rest ranking ability across the three outcomes; higher is better.\n"
            "- **PR-AUC:** macro average precision across the outcomes; higher is better.\n"
            "- **Brier score:** mean squared error between predicted class probabilities and the "
            "one-hot outcome; lower is better.\n\n"
            "The paired bootstrap resamples the same test sessions for both variants. Its confidence "
            "interval describes uncertainty in the paired score difference. In this implementation, "
            "the reported `p` is the fraction of bootstrap resamples where the difference is non-positive, "
            "not a conventional null-hypothesis p-value. For C+R versus C, the macro-F1 interval "
            "(-0.0157 to +0.0219) includes zero and `p = 0.379`, so the difference is inconclusive. "
            "For ROC-AUC, the reported delta is +0.0035 and `p = 0.045`, but its 95% interval "
            "(-0.0005 to +0.0075) also includes zero; this is not unambiguous evidence of a robust gain. "
            "The report's other bootstrap comparisons and values remain below."
        )

    st.markdown(report("stage8/intent.md"))
    st.markdown(
        "**Early prediction:** “Events observed” is the number of usable events in the session prefix "
        "provided to the model; the terminal purchase/exit event is excluded by the prediction-prefix "
        "rule. The existing chart plots macro-F1 and ROC-AUC at 3, 5, 10, 20, and full-prefix lengths. "
        "The displayed curves generally improve as more behaviour becomes visible. Earlier estimates "
        "could support earlier audience analysis or action timing if separately validated; this chart "
        "only establishes predictive performance at these prefix lengths on the held-out test split, "
        "not that acting earlier improves business outcomes.\n\n"
        "**Reliability (purchase class):** The horizontal axis groups predictions by predicted purchase "
        "probability; the vertical axis shows the observed purchase frequency in each group. The dashed "
        "diagonal is perfect calibration. Points below it mean the model predicted a higher purchase "
        "probability than was observed; points above mean it predicted lower. In the high-probability "
        "bins, both displayed curves fall below the reference line, indicating overprediction there."
    )
    c1, c2 = st.columns(2)
    for col, img in ((c1, "early_curve.png"), (c2, "reliability.png")):
        p = config.REPORTS / "stage8" / img
        if p.exists():
            col.image(str(p))

    st.header("Next-Best-Action Intelligence")
    st.caption("Stage 8b — contextual bandit simulation")
    st.markdown(
        "**What are we testing?** Whether a policy can learn which of four actions to choose from "
        "predicted intent and device context in a simulated environment.\n\n"
        "**Why does it matter?** This tests an approach for adapting actions to session context; it does "
        "not measure the effect of those actions on real customers.\n\n"
        "**How was it tested?** The report simulates 30,000 decisions by repeatedly sampling held-out "
        "test sessions. Policies face common random outcomes so their simulated rewards are comparable. "
        "Purchase response is generated from the session's outcome and hidden simulator persona; the "
        "selected action's cost is subtracted from the purchase indicator.\n\n"
        "**What did we find?** LinUCB has the highest reported total reward (**9,348.08**); Thompson "
        "Sampling totals **9,218.16**, above the static rule's **7,953.68**. The Thompson Sampling "
        "versus static-rule cumulative difference becomes and remains positive after 50 decisions. "
        "In the action-share windows, comparison helper grows from **0.376** in the first window to "
        "**0.682** in the last; none falls from **0.248** to **0.031**, discount nudge from **0.211** "
        "to **0.048**, while social proof moves from **0.165** to **0.239** across the first and last "
        "windows.\n\n"
        "**What should we conclude?** These are simulation outcomes under the coded response and cost "
        "assumptions. They do not demonstrate measured improvements in real-world revenue, conversion, "
        "or customer behaviour."
    )
    with st.expander("How the bandit strategies and reward are defined"):
        st.markdown(
            "- **LinUCB:** estimates reward from a six-value intent/device context for each action and "
            "adds an uncertainty bonus when choosing; the action cost is subtracted from its score.\n"
            "- **Thompson Sampling:** keeps Beta-Bernoulli purchase estimates for each predicted-outcome "
            "and device context/action pair, samples a purchase rate, then accounts for action cost.\n"
            "- **Random:** chooses uniformly among the four actions.\n"
            "- **Static rule:** gives a discount nudge when predicted intent is abandon; otherwise "
            "chooses none.\n"
            "- **Always none:** never presents an intervention.\n"
            "- **Oracle (expected):** uses the hidden simulator persona and outcome to select the action "
            "with the highest expected net reward. It has information unavailable to the learning "
            "policies and is an idealized benchmark, not an implementable policy.\n\n"
            "For each decision, reward is the simulated purchase indicator (0 or 1) minus the action "
            "cost: none **0.00**, discount nudge **0.08**, comparison helper **0.01**, and social proof "
            "**0.01**. The simulated purchase probability is the outcome-specific base rate plus the "
            "remaining probability multiplied by the persona/action uplift.\n\n"
            "**Total reward** sums decision rewards; **mean reward per decision** divides that sum by "
            "30,000; **mean reward over the last 10,000 decisions** summarizes the final portion of the "
            "run; **regret versus oracle** is oracle expected total reward minus each policy's total."
        )
    st.markdown(report("stage8/bandit.md"))
    st.markdown(
        "**Cumulative reward chart:** Each line accumulates simulated purchase indicators minus action "
        "cost over decisions. A steeper line indicates faster accumulation in this simulation; the "
        "oracle line is expected reward and has privileged knowledge. The companion chart plots the "
        "cumulative difference between Thompson Sampling and the static rule: above zero means Thompson "
        "Sampling has accumulated more reward to that point, not that it caused more real purchases.\n\n"
        "**Actions and action-share chart:** None means no intervention; discount nudge offers a discount; "
        "comparison helper assists product comparison; social proof presents social evidence. The table "
        "and stacked area chart show each action's share per successive 3,000-decision window. In this "
        "run, comparison helper becomes the most frequent action while the none and discount shares "
        "generally decline; this is the simulated policy's learned choice distribution, not a measured "
        "customer preference."
    )
    c1, c2 = st.columns(2)
    for col, img in ((c1, "bandit_reward.png"), (c2, "bandit_actions.png")):
        p = config.REPORTS / "stage8" / img
        if p.exists():
            col.image(str(p))

# ------------------------------------------------------------------ copilot
with tabs[4]:
    import os
    from biq.copilot import ask
    if config.LLM_PROVIDER == "ollama":
        mode = f"local LLM via Ollama ({config.LLM_MODEL})"
    elif os.getenv("ANTHROPIC_API_KEY"):
        mode = f"Claude ({config.LLM_MODEL})"
    else:
        mode = "offline mode (set ANTHROPIC_API_KEY, or BIQ_LLM_PROVIDER=ollama, for open questions)"
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

    evidently_dir = config.REPORTS / "evidently"
    evidently_reports = sorted(evidently_dir.glob("*.html")) if evidently_dir.exists() else []
    if evidently_reports:
        st.markdown("#### Evidently AI data-drift report")
        choice = st.selectbox("Scenario", [p.stem for p in evidently_reports])
        html = (evidently_dir / f"{choice}.html").read_text(encoding="utf-8")
        components.html(html, height=800, scrolling=True)
    else:
        st.caption("No Evidently AI reports yet - set `BIQ_EVIDENTLY=1` with `.venv-evidently` set "
                   "up (see `requirements-evidently.txt`) and re-run the mlops stage to generate them.")

    st.markdown(report("stage9_copilot.md"))
    st.caption("Experiment tracking and the model registry: run `mlflow ui --backend-store-uri sqlite:///mlflow.db`.")
