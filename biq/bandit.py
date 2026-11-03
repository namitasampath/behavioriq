"""Stage 8b - contextual-bandit personalization.

Context  = intent predicted by the C+R model (argmax bucket + probabilities) and device.
Actions  = none, discount_nudge, compare_helper, social_proof.
Policies = Thompson Sampling (Beta-Bernoulli per context x action), LinUCB, and baselines
           (always none, random, static rule "discount if predicted abandon").

Rewards come from a simulated response model in which each action helps a different hidden
persona (e.g. discounts help deal hunters, comparison help helps heavy comparers) and discounts
cost money. The bandit never sees the persona - it must learn from intent context and reward.
Limitation (state it in the paper): this is only possible on the simulator; a logged real
dataset doesn't reveal counterfactual outcomes.

    python -m biq.bandit
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config
from .schema import OUTCOMES

ACTIONS = ["none", "discount_nudge", "compare_helper", "social_proof"]
COST = {"none": 0.0, "discount_nudge": 0.08, "compare_helper": 0.01, "social_proof": 0.01}
UPLIFT = {  # share of the remaining (1 - base) purchase probability an action converts
    "fast_converter": {"discount_nudge": 0.02, "compare_helper": 0.00, "social_proof": 0.02},
    "heavy_comparer": {"discount_nudge": 0.05, "compare_helper": 0.30, "social_proof": 0.05},
    "explorer":       {"discount_nudge": 0.03, "compare_helper": 0.04, "social_proof": 0.12},
    "deal_hunter":    {"discount_nudge": 0.35, "compare_helper": 0.03, "social_proof": 0.05},
    "abandoner":      {"discount_nudge": 0.18, "compare_helper": 0.06, "social_proof": 0.04},
}
BASE = {"purchase": 0.85, "abandon": 0.10, "browse": 0.04}


def purchase_prob(persona: str, outcome: str, action: str) -> float:
    b = BASE[outcome]
    return b + (1 - b) * UPLIFT[persona].get(action, 0.0)


def context_key(probs: np.ndarray, device: str) -> str:
    return f"{OUTCOMES[int(np.argmax(probs))]}|{'mobile' if device == 'mobile' else 'other'}"


class ThompsonSampling:
    name = "Thompson Sampling"

    def __init__(self, rng):
        self.rng, self.a, self.b = rng, {}, {}

    def choose(self, ctx, x):
        best, best_v = None, -1e9
        for act in ACTIONS:
            k = (ctx, act)
            theta = self.rng.beta(self.a.get(k, 1.0), self.b.get(k, 1.0))
            v = theta - COST[act]
            if v > best_v:
                best, best_v = act, v
        return best

    def update(self, ctx, x, act, bought):
        k = (ctx, act)
        self.a[k] = self.a.get(k, 1.0) + bought
        self.b[k] = self.b.get(k, 1.0) + (1 - bought)

    def state(self):
        return {f"{c}::{a}": [self.a[(c, a)], self.b.get((c, a), 1.0)] for (c, a) in self.a}


class LinUCB:
    name = "LinUCB"

    def __init__(self, rng, d=6, alpha=0.5):
        self.A = {a: np.eye(d) for a in ACTIONS}
        self.bv = {a: np.zeros(d) for a in ACTIONS}
        self.alpha = alpha

    def choose(self, ctx, x):
        scores = {}
        for a in ACTIONS:
            Ainv = np.linalg.inv(self.A[a])
            th = Ainv @ self.bv[a]
            scores[a] = th @ x + self.alpha * np.sqrt(x @ Ainv @ x) - COST[a]
        return max(scores, key=scores.get)

    def update(self, ctx, x, act, bought):
        self.A[act] += np.outer(x, x)
        self.bv[act] += bought * x


class Fixed:
    def __init__(self, name, fn):
        self.name, self.fn = name, fn

    def choose(self, ctx, x):
        return self.fn(ctx)

    def update(self, *a):
        pass


def simulate(n_steps: int = 30000, seed: int = config.SEED):
    s = pd.read_parquet(config.DATA / "sessions_split.parquet")
    truth = pd.read_parquet(config.TRUTH)
    test = s[s.split == "test"].merge(truth[["session_id", "persona"]], on="session_id").reset_index(drop=True)
    probs = np.load(config.DATA / "test_pred_cr.npy")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(test), n_steps)
    rngs = {}
    rand_rng = np.random.default_rng(seed + 1)
    policies = [
        ThompsonSampling(np.random.default_rng(seed + 2)),
        LinUCB(None),
        Fixed("Static rule (discount if predicted abandon)", lambda c: "discount_nudge" if c.startswith("abandon") else "none"),
        Fixed("Always none", lambda c: "none"),
        Fixed("Random", lambda c: ACTIONS[rand_rng.integers(len(ACTIONS))]),
    ]
    outcome_rng = np.random.default_rng(seed + 3)
    u = outcome_rng.random(n_steps)  # common random numbers: every policy faces the same luck
    log = {p.name: [] for p in policies}
    actions = {p.name: [] for p in policies}
    oracle = []
    for t, i in enumerate(idx):
        r = test.iloc[i]
        ctx = context_key(probs[i], r.device)
        x = np.r_[probs[i], float(r.device == "mobile"), 1.0, probs[i][1] * float(r.device == "mobile")]
        exp = {a: purchase_prob(r.persona, r.outcome, a) - COST[a] for a in ACTIONS}
        oracle.append(max(exp.values()))
        for p in policies:
            a = p.choose(ctx, x)
            bought = int(u[t] < purchase_prob(r.persona, r.outcome, a))
            p.update(ctx, x, a, bought)
            log[p.name].append(bought - COST[a])
            actions[p.name].append(a)
    rewards = pd.DataFrame(log)
    rewards["oracle (expected)"] = oracle
    return rewards, actions, policies[0]


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = config.REPORTS / "stage8"
    out.mkdir(exist_ok=True, parents=True)
    rewards, actions, ts = simulate()
    cum = rewards.cumsum()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    for c in cum.columns:
        axes[0].plot(cum.index, cum[c], label=c, lw=1.6, ls="--" if "oracle" in c else "-")
    axes[0].set_title("Cumulative reward (purchases minus action cost)"); axes[0].set_xlabel("decisions"); axes[0].legend(fontsize=8)
    rule = "Static rule (discount if predicted abandon)"
    gap = cum["Thompson Sampling"] - cum[rule]
    axes[1].plot(gap.index, gap.values, color="C0"); axes[1].axhline(0, color="grey", lw=.8)
    axes[1].set_title("Thompson Sampling minus static rule (cumulative)"); axes[1].set_xlabel("decisions")
    fig.tight_layout(); fig.savefig(out / "bandit_reward.png", dpi=130); plt.close(fig)

    acts = pd.DataFrame({"t": range(len(actions["Thompson Sampling"])), "a": actions["Thompson Sampling"]})
    acts["window"] = acts.t // 3000
    share = acts.groupby("window").a.value_counts(normalize=True).unstack().fillna(0)[ACTIONS]
    fig, ax = plt.subplots(figsize=(7, 3.8))
    share.plot.area(ax=ax, alpha=.8); ax.set_xlabel("window of 3,000 decisions"); ax.set_ylabel("share of actions")
    ax.set_title("Thompson Sampling action mix shifts as it learns"); fig.tight_layout()
    fig.savefig(out / "bandit_actions.png", dpi=130); plt.close(fig)

    warm = next((int(i) for i in gap.index if i >= 50 and (gap.loc[i:] > 0).all()), None)
    summary = pd.DataFrame({"total_reward": rewards.sum(), "mean_reward_per_decision": rewards.mean(),
                            "mean_reward_last_10k": rewards.tail(10000).mean()}).round(4)
    summary["regret_vs_oracle"] = (rewards["oracle (expected)"].sum() - rewards.sum()).round(1)
    report = ["# Stage 8 - Contextual bandit (simulated, 30,000 decisions on test sessions)\n",
              summary.sort_values("total_reward", ascending=False).to_markdown(), "",
              f"Thompson Sampling permanently overtakes the static rule after **{warm} decisions** (warm-up window)."
              if warm is not None else "Thompson Sampling did not permanently overtake the static rule.",
              "\n## Thompson Sampling action share per 3,000-decision window\n", share.round(3).to_markdown(), "",
              "Figures: bandit_reward.png, bandit_actions.png. Reward = purchase (0/1) minus action cost; "
              "response model in biq/bandit.py (UPLIFT, COST).\n"]
    (out / "bandit.md").write_text("\n".join(report), encoding="utf-8")
    (config.ARTIFACTS / "bandit_state.json").write_text(json.dumps(ts.state()))
    print("\n".join(report))


if __name__ == "__main__":
    main()
