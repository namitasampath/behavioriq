"""Stage 9 - LLM-powered natural-language analytics copilot.

Tools given to the LLM:
  describe_schema()                -> whitelisted views + column docs
  run_sql(query)                   -> guarded SQL over the warehouse
  find_similar_sessions(...)       -> Qdrant neighbours, returned only as outcome summaries
Guardrails:
  * sqlglot parse: exactly one SELECT, only whitelisted v_* views, LIMIT added, read-only connection
  * k-anonymity floor: rows whose session_count < K_ANON_MIN are suppressed
  * grounding check: every number in the answer must appear in (or round from) a tool result,
    otherwise the answer is regenerated once and then refused

With ANTHROPIC_API_KEY set it uses Claude tool use (model from BIQ_LLM_MODEL). Without a key it
falls back to a small rule-based router so the demo still runs (clearly labelled "offline mode").

    python -m biq.copilot "Which segment has the highest abandonment rate?"
    python -m biq.copilot --eval
"""
from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd
import sqlglot
from sqlglot import exp

from . import config
from .warehouse import VIEW_DOCS, VIEWS, connect

SYSTEM = f"""You are BehaviorIQ, an analytics copilot over a behavioral clickstream warehouse.
Rules:
- Answer ONLY from tool results. Never estimate, never use outside knowledge for figures.
- Call describe_schema first if unsure which view to use. You may only query these views: {', '.join(VIEWS)}.
- Every figure you state must come from a tool result; say how many sessions it is based on (session_count).
- Rates are stored as fractions 0-1; you may present them as percentages (e.g. 0.2981 -> 29.81%).
- If rows were suppressed by the privacy floor or the tools cannot answer, say so plainly.
- Keep answers to 2-4 sentences."""


class GuardError(Exception):
    pass


def guard_sql(query: str) -> str:
    try:
        trees = sqlglot.parse(query, read="duckdb")
    except Exception as e:
        raise GuardError(f"could not parse SQL: {e}")
    if len(trees) != 1 or trees[0] is None:
        raise GuardError("exactly one statement is allowed")
    tree = trees[0]
    if not isinstance(tree, (exp.Select, exp.Union)) or tree.find(exp.Insert, exp.Update, exp.Delete, exp.Drop,
                                                                  exp.Create, exp.Alter, exp.Command):
        raise GuardError("only SELECT queries are allowed")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if name not in VIEWS and name not in ctes:
            raise GuardError(f"table '{t.name}' is not whitelisted; allowed: {', '.join(VIEWS)}")
    if tree.args.get("limit") is None:
        tree = tree.limit(1000)
    return tree.sql(dialect="duckdb")


def run_sql(query: str) -> dict:
    try:
        safe = guard_sql(query)
    except GuardError as e:
        return {"error": str(e), "blocked": True}
    con = connect(read_only=True)
    try:
        df = con.execute(safe).df()
    except Exception as e:
        return {"error": str(e), "sql": safe}
    finally:
        con.close()
    suppressed = 0
    if "session_count" in df.columns:
        low = df.session_count < config.K_ANON_MIN
        suppressed = int(low.sum())
        df = df[~low]
    df = df.head(50)
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]) or df[c].dtype == object:
            df[c] = df[c].astype(str)
    return {"sql": safe, "rows": json.loads(df.to_json(orient="records")), "row_count": len(df),
            "suppressed_rows_below_k_anonymity": suppressed, "k_anonymity_floor": config.K_ANON_MIN}


def describe_schema() -> dict:
    con = connect(read_only=True)
    out = {}
    for v in VIEWS:
        cols = con.execute(f"DESCRIBE {v}").df()[["column_name", "column_type"]].values.tolist()
        out[v] = {"description": VIEW_DOCS[v], "columns": cols}
    con.close()
    return out


def find_similar_sessions(session_id: str | None = None, behaviour: list[str] | None = None, k: int = 20) -> dict:
    """Neighbours of a stored session, or of a described behaviour (a list of event types)."""
    import numpy as np

    from . import encoder as E, retrieval as R
    idx = pd.read_parquet(config.DATA / "embedding_index.parquet")
    if session_id:
        hit = idx.index[idx.session_id == session_id]
        if len(hit) == 0:
            return {"error": f"unknown session {session_id}"}
        vec = np.load(config.DATA / "emb_custom.npy")[hit[0]]
    elif behaviour:
        b = E.EncoderBundle.load("encoder")
        vec = b.embed(behaviour, [None] * len(behaviour), [10.0] * len(behaviour))
    else:
        return {"error": "give session_id or behaviour"}
    nb = R.neighbors(vec, k, "biq_custom", exclude_session=session_id)
    summ = R.outcome_summary(nb)
    return {"neighbours_considered": len(nb), "outcome_counts": summ,
            "outcome_share": {o: round(c / len(nb), 4) for o, c in summ.items()},
            "mean_similarity": round(float(np.mean([n["score"] for n in nb])), 4)}


TOOLS = [
    {"name": "describe_schema", "description": "List the whitelisted analytic views and their columns.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "run_sql", "description": "Run ONE read-only DuckDB SELECT over the whitelisted v_* views. "
                                       "Rows with session_count below the privacy floor are removed.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "find_similar_sessions",
     "description": "Find historical sessions similar to a session id or to a behaviour described as an ordered list "
                    "of event types (from: page_view, search, click, scroll, hover, product_view, review_view, compare, "
                    "add_to_cart, remove_from_cart, checkout_start). Returns outcome counts of the neighbours.",
     "input_schema": {"type": "object", "properties": {
         "session_id": {"type": "string"}, "behaviour": {"type": "array", "items": {"type": "string"}},
         "k": {"type": "integer", "default": 20}}}},
]
IMPL = {"describe_schema": describe_schema, "run_sql": run_sql, "find_similar_sessions": find_similar_sessions}


# ---------------------------------------------------------------- grounding
NUM = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?%?")


def _numbers_in(obj) -> set[float]:
    vals = set()
    for m in NUM.findall(json.dumps(obj)):
        try:
            vals.add(float(m.replace(",", "").rstrip("%")))
        except ValueError:
            pass
    return vals


def grounded(answer: str, tool_outputs: list) -> tuple[bool, list[str]]:
    src = set()
    for o in tool_outputs:
        src |= _numbers_in(o)
    derived = src | {round(v * 100, d) for v in src for d in (0, 1, 2)} | {round(v, d) for v in src for d in (0, 1, 2)}
    bad = []
    for m in NUM.findall(answer):
        v = float(m.replace(",", "").rstrip("%"))
        if v in (0, 1, 2, 3, 4, 5, 10, 20, 100) and not m.endswith("%"):  # small counting words / k
            continue
        if not any(abs(v - d) <= max(0.011, abs(d) * 0.0005) for d in derived):
            bad.append(m)
    return not bad, bad


# ---------------------------------------------------------------- agent loops
def ask_llm(question: str, max_tool_calls: int = 6) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    msgs = [{"role": "user", "content": question}]
    outputs, sqls = [], []
    for attempt in range(2):
        calls = 0
        while True:
            resp = client.messages.create(model=config.LLM_MODEL, max_tokens=800, system=SYSTEM, tools=TOOLS, messages=msgs)
            if resp.stop_reason != "tool_use" or calls >= max_tool_calls:
                break
            msgs.append({"role": "assistant", "content": resp.content})
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    calls += 1
                    out = IMPL[b.name](**(b.input or {}))
                    outputs.append(out)
                    if b.name == "run_sql":
                        sqls.append(out.get("sql", b.input.get("query")))
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)[:12000]})
            msgs.append({"role": "user", "content": results})
        answer = "".join(b.text for b in resp.content if b.type == "text").strip()
        ok, bad = grounded(answer, outputs)
        if ok:
            return {"answer": answer, "grounded": True, "sql": sqls, "tool_outputs": outputs, "mode": config.LLM_MODEL}
        msgs.append({"role": "assistant", "content": resp.content})
        msgs.append({"role": "user", "content": f"These numbers are not in any tool result: {bad}. "
                                                 "Rewrite the answer using only figures from tool results."})
    return {"answer": "I can't answer that reliably from the data available.", "grounded": False,
            "unfounded": bad, "sql": sqls, "tool_outputs": outputs, "mode": config.LLM_MODEL}


def _fmt_pct(x):
    return f"{100 * x:.2f}%"


def _n(x):
    return f"{int(round(float(x))):,}"


def ask_offline(question: str) -> dict:
    """Rule-based router used when no API key is configured. Same tools, same guardrails."""
    q = question.lower()
    outputs, sqls = [], []

    def sql(query):
        o = run_sql(query); outputs.append(o); sqls.append(o.get("sql", query)); return o

    if "similar" in q or "like users who" in q:
        beh = ["search", "product_view", "compare", "product_view", "compare", "add_to_cart"] if "compar" in q else \
              ["product_view", "add_to_cart", "remove_from_cart"]
        o = find_similar_sessions(behaviour=beh, k=20); outputs.append(o)
        top = max(o["outcome_share"], key=o["outcome_share"].get)
        ans = (f"Among the {o['neighbours_considered']} most similar historical sessions, the most common outcome is "
               f"{top} ({_fmt_pct(o['outcome_share'][top])}); counts: {o['outcome_counts']}.")
    elif "segment" in q or "persona" in q:
        col = "abandonment_rate" if "abandon" in q else "conversion_rate"
        o = sql(f"SELECT segment, session_count, {col} FROM v_segment_summary ORDER BY {col} DESC LIMIT 1")
        r = o["rows"][0]
        ans = f"The segment '{r['segment']}' has the highest {col.replace('_', ' ')}: {_fmt_pct(r[col])}, based on {_n(r['session_count'])} sessions."
    elif any(w in q for w in ("device", "mobile", "desktop", "tablet")):
        o = sql("SELECT device, SUM(session_count) AS session_count, ROUND(SUM(conversion_rate*session_count)/SUM(session_count),4) AS conversion_rate "
                "FROM v_conversion_by_segment GROUP BY device ORDER BY conversion_rate DESC")
        rows = o["rows"]
        asked = [r for r in rows if r["device"] in q]
        lead = (f"Conversion on {asked[0]['device']} is {_fmt_pct(asked[0]['conversion_rate'])} "
                f"({_n(asked[0]['session_count'])} sessions). ") if asked else ""
        ans = lead + "By device: " + "; ".join(f"{r['device']} {_fmt_pct(r['conversion_rate'])} ({_n(r['session_count'])} sessions)" for r in rows) + "."
    elif "average" in q and "event" in q:
        o = sql("SELECT outcome, session_count, avg_events FROM v_outcome_summary ORDER BY outcome")
        ans = "Average events per session: " + "; ".join(f"{r['outcome']} {r['avg_events']} ({_n(r['session_count'])} sessions)" for r in o["rows"]) + "."
    elif "day" in q:
        o = sql("SELECT day, session_count, conversion_rate FROM v_funnel_daily ORDER BY session_count DESC LIMIT 1")
        r = o["rows"][0]
        ans = f"The busiest day was {r['day']} with {_n(r['session_count'])} sessions (conversion {_fmt_pct(r['conversion_rate'])})."
    elif "compar" in q:
        o = sql("SELECT compare_events_capped, session_count, abandonment_rate FROM v_compare_abandonment ORDER BY 1")
        ans = "Abandonment by number of compare events: " + "; ".join(
            f"{r['compare_events_capped']}{'+' if r['compare_events_capped'] == 4 else ''} compares -> {_fmt_pct(r['abandonment_rate'])} ({r['session_count']} sessions)" for r in o["rows"]) + "."
    elif "category" in q:
        o = sql("SELECT category, session_count, cart_rate FROM v_category_performance ORDER BY cart_rate DESC")
        r = o["rows"][0]
        ans = f"'{r['category']}' has the highest add-to-cart rate per product view: {_fmt_pct(r['cart_rate'])} across {_n(r['session_count'])} sessions."
    elif "conversion" in q or "convert" in q:
        o = sql("SELECT SUM(session_count) AS session_count, ROUND(SUM(purchased)/SUM(session_count),4) AS conversion_rate FROM v_funnel_daily")
        r = o["rows"][0]
        ans = f"Overall conversion rate is {_fmt_pct(r['conversion_rate'])} across {_n(r['session_count'])} sessions."
    elif "how many" in q or "sessions" in q:
        o = sql("SELECT outcome, session_count FROM v_outcome_summary ORDER BY session_count DESC")
        ans = "Sessions by outcome: " + "; ".join(f"{r['outcome']} {_n(r['session_count'])}" for r in o["rows"]) + "."
    else:
        return {"answer": "Offline mode can answer questions about conversion, segments, devices, categories, compares, "
                          "outcomes and similar sessions. Set ANTHROPIC_API_KEY for open-ended questions.",
                "grounded": True, "sql": [], "tool_outputs": [], "mode": "offline"}
    ok, bad = grounded(ans, outputs)
    return {"answer": ans, "grounded": ok, "unfounded": bad, "sql": sqls, "tool_outputs": outputs, "mode": "offline"}


def ask(question: str) -> dict:
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            return ask_llm(question)
        except Exception as e:  # network / quota problems -> still answer
            r = ask_offline(question); r["mode"] = f"offline (LLM error: {type(e).__name__})"; return r
    return ask_offline(question)


# ---------------------------------------------------------------- evaluation (Stage 9 DoD)
EVAL = [  # (question, gold SQL, gold value column, how to pick)
    ("What is the overall conversion rate?", "SELECT ROUND(SUM(purchased)/SUM(session_count),4) v FROM v_funnel_daily"),
    ("How many sessions ended in abandonment?", "SELECT session_count v FROM v_outcome_summary WHERE outcome='abandon'"),
    ("How many sessions ended in a purchase?", "SELECT session_count v FROM v_outcome_summary WHERE outcome='purchase'"),
    ("Which behavioral segment has the highest abandonment rate?", "SELECT abandonment_rate v FROM v_segment_summary ORDER BY abandonment_rate DESC LIMIT 1"),
    ("Which segment has the highest conversion rate?", "SELECT conversion_rate v FROM v_segment_summary ORDER BY conversion_rate DESC LIMIT 1"),
    ("What is the conversion rate on mobile?", "SELECT ROUND(SUM(conversion_rate*session_count)/SUM(session_count),4) v FROM v_conversion_by_segment WHERE device='mobile'"),
    ("What is the conversion rate on desktop?", "SELECT ROUND(SUM(conversion_rate*session_count)/SUM(session_count),4) v FROM v_conversion_by_segment WHERE device='desktop'"),
    ("What is the abandonment rate for sessions with no compare events?", "SELECT abandonment_rate v FROM v_compare_abandonment WHERE compare_events_capped=0"),
    ("What is the abandonment rate for sessions with 4 or more compare events?", "SELECT abandonment_rate v FROM v_compare_abandonment WHERE compare_events_capped=4"),
    ("Which product category has the highest add-to-cart rate?", "SELECT cart_rate v FROM v_category_performance ORDER BY cart_rate DESC LIMIT 1"),
    ("How many sessions were browse-only?", "SELECT session_count v FROM v_outcome_summary WHERE outcome='browse'"),
    ("What is the average number of events in purchasing sessions?", "SELECT avg_events v FROM v_outcome_summary WHERE outcome='purchase'"),
    ("Which day had the most sessions and how many?", "SELECT session_count v FROM v_funnel_daily ORDER BY session_count DESC LIMIT 1"),
    ("How many sessions are in the cart abandoners segment?", "SELECT SUM(session_count) v FROM v_segment_summary WHERE segment LIKE 'cart abandoners%'"),
    ("What do sessions similar to users who compared multiple products and added to cart usually end in?", None),
    ("Find sessions similar to users who added to cart then removed the item: how do they usually end?", None),
]


def evaluate():
    rows = []
    con = connect(read_only=True)
    for q, gold_sql in EVAL:
        r = ask(q)
        if gold_sql:
            g = float(con.execute(gold_sql).fetchone()[0])
            cands = {g, round(g * 100, 2), round(g * 100, 1), round(g, 2)}
            nums = [float(m.replace(",", "").rstrip("%")) for m in NUM.findall(r["answer"])]
            correct = any(abs(n - c) < 0.006 for n in nums for c in cands)
        else:
            correct = bool(r["tool_outputs"]) and "outcome_counts" in json.dumps(r["tool_outputs"])
        rows.append(dict(question=q, correct=correct, grounded=r["grounded"], mode=r["mode"], answer=r["answer"][:160]))
    con.close()
    # guardrail tests
    blocked = [run_sql(x).get("blocked", False) for x in
               ["DROP TABLE fact_sessions", "SELECT * FROM fact_user_events", "SELECT 1; DELETE FROM fact_sessions",
                "SELECT * FROM v_outcome_summary; DROP VIEW v_outcome_summary"]]
    df = pd.DataFrame(rows)
    rep = (f"# Stage 9 - Copilot evaluation ({len(df)} questions, mode: {df['mode'].iloc[0]})\n\n"
           f"- Correct: {df.correct.sum()}/{len(df)} ({100 * df.correct.mean():.0f}%)\n"
           f"- Answers with unfounded numbers: {(~df.grounded).sum()}\n"
           f"- Malicious / non-whitelisted SQL blocked: {sum(blocked)}/{len(blocked)}\n\n"
           + ("> Offline mode uses hand-written routes built alongside these questions, so its score shows the tools, "
              "guardrails and grounding check work - it is NOT evidence of LLM accuracy. Set ANTHROPIC_API_KEY and rerun "
              "`python -m biq.copilot --eval` for the real Stage 9 result.\n\n" if df["mode"].iloc[0] == "offline" else "")
           + df.to_markdown(index=False) + "\n")
    (config.REPORTS / "stage9_copilot.md").write_text(rep, encoding="utf-8")
    print(rep)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--eval":
        evaluate()
    else:
        print(json.dumps(ask(" ".join(sys.argv[1:]) or "Which segment has the highest abandonment rate?"), indent=1, default=str)[:3000])
