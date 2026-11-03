# Contracts (do not change silently; code source of truth: `biq/schema.py`)

## Event schema
`event_id` (uuid), `session_id`, `anon_user_id` (random, never derived from identity), `event_type`,
`timestamp` (UTC ISO-8601), `page`, `product_id`, `category`, `device` (desktop|mobile|tablet),
`referrer`, `meta` {`scroll_depth` 0-1, `dwell_ms`, `seq` per-session sequence number}.
No names, emails, passwords or free text are ever captured; search query text is not sent.

## Vocabulary (13)
page_view, search, click, scroll, hover · product_view, review_view, compare ·
add_to_cart, remove_from_cart, checkout_start, purchase · exit

## Session rule
Ordered events sharing a session_id, closed after 30 minutes of inactivity OR an explicit
purchase/exit event, whichever comes first.

## Outcome label
purchase if the session has a purchase; abandon if it has add_to_cart but no purchase; else browse.

## Prediction prefix (leakage rule)
Intent models only see events before the first purchase/exit event.

## Splits and retrieval (leakage rules)
Time-based split on session close time: 70% train / 15% validation / 15% test.
Retrieval returns only train sessions that closed before the query session started, never itself.

## Privacy
Copilot may query only the whitelisted `v_*` views; rows with session_count < K_ANON_MIN (20) are suppressed.

## Real-data replay (REES46)
`python run_all.py --dataset rees46` (`biq/replay.py`) replays the REES46 "eCommerce behavior data
from multi category store" CSV (`data/external/*.csv`) into the schema above instead of the
simulator. What the source data can and cannot give us:

- **Event vocabulary.** REES46 only has `view`, `cart`, `remove_from_cart`, `purchase` (the Oct
  2019 file used here has no `remove_from_cart` rows at all). It maps onto four of our thirteen
  event types (`product_view`, `add_to_cart`, `remove_from_cart`, `purchase`); it has **no**
  `search`, `click`, `scroll`, `hover`, `review_view`, `compare`, `checkout_start` or `exit`
  events. Every session is therefore a `product_view`/`add_to_cart`/`purchase` sequence with none
  of the consideration/navigation signal the simulator fabricates - mining's sequence features
  and the "compare"/"review" based intent signals are structurally zero on this dataset, and
  sessions never close on `exit`, only on purchase or 30-minute inactivity.
- **No device or referrer.** REES46 has neither column. Since the schema requires both, every
  replayed event is stamped `device="desktop"`, `referrer="direct"` as a placeholder; this is
  fabricated, not measured, and device/referrer-segmented results must be ignored for this
  dataset (dashboard device split, bandit `is_mobile` context, etc.).
- **No hidden personas.** The simulator's `persona` ground truth (`sessions_truth.parquet`) does
  not exist for real data - `mining.run()` skips the ARI-vs-persona row, and the bandit stage
  (which needs a hidden-persona response model for counterfactual rewards) is skipped outright.
- **Re-sessionisation.** REES46 ships its own `user_session` id. `biq/replay.py` re-derives
  session boundaries from scratch using OUR rule (30 min inactivity, or a purchase, closes a
  session) rather than trusting REES46's grouping, and reports how many source sessions were
  split as a result (see docs/PROGRESS.md / docs/RESULTS_FOR_REPORT.md for the measured rate).
- **Anonymisation.** `user_id` and the re-sessionised id are salted SHA-256 hashes
  (`biq/replay.py:SALT`), never the raw REES46 id; `event_id` is freshly generated.
- `dwell_ms` is a real inter-event gap (ms since the previous event in the same final session, 0
  for the first event of a session) instead of the simulator's synthetic exponential dwell.
