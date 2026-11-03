"""Stage 6 - sequence representation learning.

A compact SASRec/BERT4Rec-style Transformer encoder turns a session (tokens = event_type|category,
plus a log time-gap bucket) into a fixed-size L2-normalised embedding. It is pretrained
self-supervised with an InfoNCE / NT-Xent contrastive loss over two augmented views of the same
session (random mask, end-anchored crop, local reorder), then optionally fine-tuned for intent.

Leakage rules (enforced here):
  * time-based split by session close time (70/15/15), never random
  * inputs are prediction prefixes: everything before the first purchase/exit event
  * vocabulary built on the train split only

    python -m biq.encoder
"""
from __future__ import annotations

import json
import math
import random

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F

from . import config
from .schema import OUTCOMES, prediction_prefix

PAD, MASK, CLS, UNK = 0, 1, 2, 3
MAX_LEN = 40
N_GAP = 16
DIM = 64


def set_seed(s: int = config.SEED):
    random.seed(s); np.random.seed(s); torch.manual_seed(s)


# ---------------------------------------------------------------- data
def time_split(sessions: pd.DataFrame) -> pd.DataFrame:
    s = sessions.sort_values("close_ts").reset_index(drop=True)
    n = len(s)
    s["split"] = np.where(np.arange(n) < 0.70 * n, "train", np.where(np.arange(n) < 0.85 * n, "val", "test"))
    return s


def prefix_view(row, length: int | None = None):
    """Events usable for prediction (no terminal token). Optionally only the first `length` of them."""
    ev = list(row.events)
    k = prediction_prefix(ev)
    if length is not None:
        k = min(k, length)
    return ev[:k], list(row.categories)[:k], list(row.gaps)[:k]


def tokens_of(events, cats=None):
    """Tokens are behavioural event types. Product category is deliberately NOT part of the token:
    with it, the contrastive encoder learned to group sessions by category instead of by behaviour
    (neighbour outcome agreement 0.455 vs 0.577 without it - see docs/DESIGN_NOTES.md)."""
    return list(events)


class Vocab:
    def __init__(self, itos: list[str]):
        self.itos = itos
        self.stoi = {t: i for i, t in enumerate(itos)}

    @classmethod
    def build(cls, token_lists):
        uniq = sorted({t for tl in token_lists for t in tl})
        return cls(["<pad>", "<mask>", "<cls>", "<unk>"] + uniq)

    def encode(self, toks):
        return [self.stoi.get(t, UNK) for t in toks]


def gap_bucket(g: float) -> int:
    return int(min(N_GAP - 1, math.floor(math.log2(1 + max(g, 0.0)))))


def to_tensor(batch: list[tuple[list[int], list[int]]]):
    """batch of (token_ids, gap_ids) -> padded tensors with a CLS token in front."""
    L = min(MAX_LEN, max(len(t) for t, _ in batch)) + 1
    tok = torch.full((len(batch), L), PAD, dtype=torch.long)
    gap = torch.zeros((len(batch), L), dtype=torch.long)
    for i, (t, g) in enumerate(batch):
        t, g = t[-(L - 1):], g[-(L - 1):]
        tok[i, 0] = CLS
        tok[i, 1:1 + len(t)] = torch.tensor(t)
        gap[i, 1:1 + len(g)] = torch.tensor(g)
    return tok, gap


# ---------------------------------------------------------------- model
class SessionEncoder(nn.Module):
    def __init__(self, vocab_size: int, dim: int = DIM, layers: int = 2, heads: int = 4, dropout: float = 0.1):
        super().__init__()
        self.tok = nn.Embedding(vocab_size, dim, padding_idx=PAD)
        self.pos = nn.Embedding(MAX_LEN + 1, dim)
        self.gap = nn.Embedding(N_GAP, dim)
        layer = nn.TransformerEncoderLayer(dim, heads, dim * 2, dropout, batch_first=True, norm_first=True)
        self.tf = nn.TransformerEncoder(layer, layers)
        self.norm = nn.LayerNorm(dim)
        self.proj = nn.Sequential(nn.Linear(dim, dim), nn.ReLU(), nn.Linear(dim, dim))  # contrastive head
        self.cls_head = nn.Linear(dim, len(OUTCOMES))

    def encode(self, tok, gap):
        pos = torch.arange(tok.size(1), device=tok.device).unsqueeze(0)
        x = self.tok(tok) + self.pos(pos) + self.gap(gap)
        h = self.tf(x, src_key_padding_mask=tok.eq(PAD))
        return self.norm(h[:, 0])

    def embed(self, tok, gap):
        return F.normalize(self.encode(tok, gap), dim=-1)


def nt_xent(z1, z2, temperature: float = 0.1):
    z = F.normalize(torch.cat([z1, z2]), dim=-1)
    sim = z @ z.t() / temperature
    n = z1.size(0)
    sim.fill_diagonal_(-1e9)
    target = torch.cat([torch.arange(n, 2 * n), torch.arange(0, n)])
    return F.cross_entropy(sim, target)


def augment(t: list[int], g: list[int], rng: random.Random):
    """Two views of one session: end-anchored crop (the most recent events carry intent, so 70% of
    crops keep the tail), 10% masking, and a local reorder inside a window of 3."""
    t, g = list(t), list(g)
    n = len(t)
    if n >= 4 and rng.random() < 0.5:
        k = max(2, int(n * rng.uniform(0.6, 0.9)))
        s = n - k if rng.random() < 0.7 else rng.randint(0, n - k)
        t, g = t[s:s + k], g[s:s + k]
    for i in range(len(t)):
        if rng.random() < 0.10:
            t[i] = MASK
    if len(t) >= 3 and rng.random() < 0.3:
        i = rng.randint(0, len(t) - 3)
        w = list(zip(t[i:i + 3], g[i:i + 3])); rng.shuffle(w)
        t[i:i + 3] = [a for a, _ in w]; g[i:i + 3] = [b for _, b in w]
    return t, g


# ---------------------------------------------------------------- training
def pretrain(model, train_items, val_items, epochs=15, batch=256, lr=2e-3, temperature=0.2, log=print):
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    rng = random.Random(config.SEED)
    best, best_state, history, patience = 1e9, None, [], 0

    def run(items, train: bool):
        model.train(train)
        idx = list(range(len(items)))
        rng.shuffle(idx)
        tot, nb = 0.0, 0
        for b in range(0, len(idx), batch):
            chunk = [items[i] for i in idx[b:b + batch]]
            if len(chunk) < 8:
                continue
            v1 = to_tensor([augment(t, g, rng) for t, g in chunk])
            v2 = to_tensor([augment(t, g, rng) for t, g in chunk])
            with torch.set_grad_enabled(train):
                loss = nt_xent(model.proj(model.encode(*v1)), model.proj(model.encode(*v2)), temperature)
                if train:
                    opt.zero_grad(); loss.backward(); opt.step()
            tot += loss.item(); nb += 1
        return tot / max(nb, 1)

    for ep in range(epochs):
        tr = run(train_items, True)
        va = run(val_items, False)
        history.append(dict(epoch=ep, train_loss=tr, val_loss=va))
        if va < best - 1e-3:
            best, best_state, patience = va, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
        if ep % 5 == 0:
            log(f"  pretrain epoch {ep:2d}  train {tr:.3f}  val {va:.3f}")
        if patience >= 6:
            break
    model.load_state_dict(best_state)
    return pd.DataFrame(history)


def finetune(model, train_items, y_train, val_items, y_val, epochs=15, batch=128, lr=5e-4):
    """Fine-tune encoder + classification head on the labeled intent task (variant C-ft)."""
    opt = torch.optim.AdamW([{"params": model.cls_head.parameters(), "lr": lr * 4},
                             {"params": [p for n, p in model.named_parameters() if not n.startswith("cls_head")], "lr": lr}])
    y_train = torch.tensor(y_train); best, best_state = 1e9, None
    for ep in range(epochs):
        model.train()
        perm = torch.randperm(len(train_items))
        for b in range(0, len(perm), batch):
            ids = perm[b:b + batch].tolist()
            tok, gap = to_tensor([train_items[i] for i in ids])
            loss = F.cross_entropy(model.cls_head(model.encode(tok, gap)), y_train[ids])
            opt.zero_grad(); loss.backward(); opt.step()
        vl = F.cross_entropy(torch.tensor(predict_proba(model, val_items)).log(), torch.tensor(y_val)).item()
        if vl < best:
            best, best_state = vl, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    return model


@torch.no_grad()
def embed_items(model, items, batch=512) -> np.ndarray:
    model.eval()
    out = [model.embed(*to_tensor(items[b:b + batch])).numpy() for b in range(0, len(items), batch)]
    return np.vstack(out) if out else np.zeros((0, DIM))


@torch.no_grad()
def predict_proba(model, items, batch=512) -> np.ndarray:
    model.eval()
    out = [F.softmax(model.cls_head(model.encode(*to_tensor(items[b:b + batch]))), -1).numpy()
           for b in range(0, len(items), batch)]
    return np.vstack(out)


# ---------------------------------------------------------------- baseline embedding
def baseline_embed(texts_train: list[str], texts_all: list[str]) -> tuple[np.ndarray, str]:
    """Generic text embedding of the stringified sequence. Uses sentence-transformers MiniLM when
    available (pip install sentence-transformers); otherwise TF-IDF n-grams + SVD."""
    try:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer("all-MiniLM-L6-v2")
        return m.encode(texts_all, batch_size=128, normalize_embeddings=True), "MiniLM-L6-v2 (sentence-transformers)"
    except Exception:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize
        vec = TfidfVectorizer(ngram_range=(1, 3), token_pattern=r"[^ ]+").fit(texts_train)
        X_train = vec.transform(texts_train)
        # a low-diversity event vocabulary (e.g. real data with far fewer event types than the
        # simulator) can yield fewer than DIM distinct n-gram features; SVD can't exceed that
        n_comp = max(1, min(DIM, X_train.shape[1] - 1))
        svd = TruncatedSVD(n_comp, random_state=config.SEED).fit(X_train)
        return normalize(svd.transform(vec.transform(texts_all))), f"TF-IDF(1-3 gram) + SVD-{n_comp} (fallback)"


class EncoderBundle:
    """Everything needed to embed a (possibly in-progress) session at serving time."""

    def __init__(self, model: SessionEncoder, vocab: Vocab):
        self.model, self.vocab = model, vocab

    def item(self, events, cats, gaps):
        return self.vocab.encode(tokens_of(events, [c or "none" for c in cats])), [gap_bucket(g) for g in gaps]

    def embed(self, events, cats, gaps) -> np.ndarray:
        return embed_items(self.model, [self.item(events, cats, gaps)])[0]

    def save(self, name="encoder"):
        torch.save(self.model.state_dict(), config.ARTIFACTS / f"{name}.pt")
        (config.ARTIFACTS / "vocab.json").write_text(json.dumps(self.vocab.itos))

    @classmethod
    def load(cls, name="encoder"):
        itos = json.loads((config.ARTIFACTS / "vocab.json").read_text())
        m = SessionEncoder(len(itos))
        m.load_state_dict(torch.load(config.ARTIFACTS / f"{name}.pt", map_location="cpu"))
        m.eval()
        return cls(m, Vocab(itos))


def neighbour_agreement(emb: np.ndarray, y: np.ndarray, k: int = 10) -> float:
    sims = emb @ emb.T
    np.fill_diagonal(sims, -9)
    nn_idx = np.argsort(-sims, axis=1)[:, :k]
    return float((y[nn_idx] == y[:, None]).mean())


def main():
    set_seed()
    s = time_split(pd.read_parquet(config.SESSIONS))
    s.to_parquet(config.DATA / "sessions_split.parquet", index=False)
    pre = [prefix_view(r) for r in s.itertuples()]
    toks = [tokens_of(e, c) for e, c, _ in pre]
    vocab = Vocab.build([t for t, sp in zip(toks, s.split) if sp == "train"])
    items = [(vocab.encode(t), [gap_bucket(g) for g in gp]) for t, (_, _, gp) in zip(toks, pre)]
    tr = [i for i, sp in zip(items, s.split) if sp == "train"]
    va = [i for i, sp in zip(items, s.split) if sp == "val"]
    print(f"vocab {len(vocab.itos)} tokens | train {len(tr)} val {len(va)} test {(s.split == 'test').sum()}")

    model = SessionEncoder(len(vocab.itos))
    hist = pretrain(model, tr, va)
    hist.to_csv(config.REPORTS / "stage6_pretrain_curve.csv", index=False)
    bundle = EncoderBundle(model, vocab)
    bundle.save("encoder")

    emb = embed_items(model, items)
    texts = [" ".join(t) for t in toks]
    base, base_name = baseline_embed([t for t, sp in zip(texts, s.split) if sp == "train"], texts)
    out = s[["session_id", "start_ts", "close_ts", "outcome", "device", "split"]].copy()
    np.save(config.DATA / "emb_custom.npy", emb)
    np.save(config.DATA / "emb_baseline.npy", base.astype(np.float32))
    out.to_parquet(config.DATA / "embedding_index.parquet", index=False)

    test = (s.split == "test").values
    y = np.asarray(s.outcome.astype(str))
    ag_c = neighbour_agreement(emb[test], y[test])
    ag_b = neighbour_agreement(base[test], y[test])
    rnd = float(pd.Series(y[test]).value_counts(normalize=True).pow(2).sum())
    report = (f"# Stage 6 - Representation sanity check\n\nBaseline encoder: {base_name}\n\n"
              f"| Embedding space | Neighbour outcome agreement @10 (test) |\n| --- | --- |\n"
              f"| Custom contrastive Transformer | {ag_c:.3f} |\n| Baseline | {ag_b:.3f} |\n"
              f"| Random (chance) | {rnd:.3f} |\n\nPretraining stopped after {len(hist)} epochs; "
              f"best val loss {hist.val_loss.min():.3f}.\n")
    (config.REPORTS / "stage6_encoder.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
