// Mirrors the canonical event contract in biq/schema.py - field names and shapes must match
// exactly, since the ingestion API validates every event against that pydantic model.

export type EventType =
  | "page_view"
  | "search"
  | "click"
  | "scroll"
  | "hover"
  | "product_view"
  | "review_view"
  | "compare"
  | "add_to_cart"
  | "remove_from_cart"
  | "checkout_start"
  | "purchase"
  | "exit";

export type Device = "desktop" | "mobile" | "tablet";

export interface EventMeta {
  scroll_depth: number;
  dwell_ms: number;
  seq: number;
}

export interface TrackedEvent {
  event_id: string;
  session_id: string;
  anon_user_id: string;
  event_type: EventType;
  timestamp: string;
  page: string;
  product_id: string | null;
  category: string | null;
  device: Device;
  referrer: string;
  meta: EventMeta;
}

/**
 * Fields a caller may pass to track(). This is a closed, named allow-list on purpose: track()
 * only ever reads these properties off the payload object, so a caller that accidentally passes
 * something else through (e.g. a raw search query string) can never reach the network - there is
 * no field in the canonical schema for it anyway.
 */
export interface TrackPayload {
  page?: string;
  product_id?: string | null;
  category?: string | null;
  device?: Device;
  referrer?: string;
}

export interface BehaviorTrackerOptions {
  /** Ingestion API /track endpoint, e.g. http://localhost:8000/track */
  ingestUrl: string;
  flushIntervalMs?: number;
  batchSize?: number;
  maxRetries?: number;
  backoffMs?: number;
  sessionTimeoutMs?: number;
  /** Override for tests: defaults to window.localStorage. */
  storage?: Storage;
  /** Override for tests: defaults to the global fetch. */
  fetchImpl?: typeof fetch;
  /** Override for tests: defaults to Date.now. */
  now?: () => number;
}
