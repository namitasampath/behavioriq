"""Fast unit tests (no trained artifacts needed).   pytest -q"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from biq import simulator  # noqa: E402
from biq.copilot import GuardError, grounded, guard_sql  # noqa: E402
from biq.encoder import augment, nt_xent  # noqa: E402
from biq.mlops import psi  # noqa: E402
from biq.schema import Event, label_session, prediction_prefix  # noqa: E402
from biq.stream_processor import SessionProcessor  # noqa: E402


def test_label_rule():
    assert label_session(["page_view", "add_to_cart", "checkout_start", "purchase"]) == "purchase"
    assert label_session(["page_view", "add_to_cart", "exit"]) == "abandon"
    assert label_session(["page_view", "product_view", "exit"]) == "browse"


def test_prefix_drops_terminal_event():
    assert prediction_prefix(["page_view", "add_to_cart", "purchase"]) == 2
    assert prediction_prefix(["page_view", "search"]) == 2


def test_schema_rejects_bad_event():
    ok = dict(event_id="e", session_id="s", anon_user_id="u", event_type="search", timestamp=datetime.now(timezone.utc),
              page="/", device="mobile", referrer="direct")
    Event(**ok)
    with pytest.raises(ValidationError):
        Event(**{**ok, "event_type": "teleport"})


def test_simulator_reproducible_and_valid():
    a, ta, _ = simulator.generate(50, seed=7)
    b, tb, _ = simulator.generate(50, seed=7)
    assert a.event_id.tolist() == b.event_id.tolist()
    assert set(ta.outcome) <= {"purchase", "abandon", "browse"}
    simulator.to_event_dict(a.iloc[0].to_dict())  # validates against schema


def test_stream_processor_closes_on_timeout_and_terminal():
    p = SessionProcessor()
    t = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = dict(device="desktop", referrer="direct", page="/", meta={"dwell_ms": 10, "scroll_depth": .1})
    p.on_event({**base, "session_id": "a", "event_type": "page_view", "timestamp": t, "seq": 0})
    p.on_event({**base, "session_id": "a", "event_type": "purchase", "timestamp": t + timedelta(seconds=5), "seq": 1})
    p.on_event({**base, "session_id": "b", "event_type": "page_view", "timestamp": t, "seq": 0})
    p.on_event({**base, "session_id": "c", "event_type": "page_view", "timestamp": t + timedelta(hours=1), "seq": 0})
    p.sweep()
    reasons = {c["session_id"]: c["close_reason"] for c in p.closed}
    assert reasons == {"a": "purchase", "b": "timeout"}


def test_sql_guard():
    assert "LIMIT" in guard_sql("SELECT * FROM v_outcome_summary").upper()
    for bad in ["DROP TABLE fact_sessions", "SELECT * FROM fact_sessions", "SELECT 1; DELETE FROM x"]:
        with pytest.raises(GuardError):
            guard_sql(bad)


def test_grounding_check():
    assert grounded("Conversion is 26.17% over 6000 sessions.", [{"rows": [{"c": 0.2617, "n": 6000}]}])[0]
    assert not grounded("Conversion is 41.5%.", [{"rows": [{"c": 0.2617}]}])[0]


def test_contrastive_loss_prefers_matching_views():
    import torch
    z = torch.randn(16, 8)
    assert nt_xent(z, z + 0.01 * torch.randn(16, 8)) < nt_xent(z, torch.randn(16, 8))


def test_augment_keeps_length_reasonable():
    import random
    t, g = augment(list(range(4, 20)), [1] * 16, random.Random(0))
    assert 2 <= len(t) <= 16 and len(t) == len(g)


def test_psi_detects_shift():
    rng = np.random.default_rng(0)
    ref = rng.normal(0, 1, 5000)
    assert psi(ref, rng.normal(0, 1, 5000)) < 0.05
    assert psi(ref, rng.normal(1, 1, 5000)) > 0.2
