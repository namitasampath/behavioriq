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
