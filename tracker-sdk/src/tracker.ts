import { IdentityStore, memoryStorage, uuidv4 } from "./storage";
import type { BehaviorTrackerOptions, Device, EventType, TrackPayload, TrackedEvent } from "./types";

const DEFAULTS = {
  flushIntervalMs: 3000,
  batchSize: 10,
  maxRetries: 3,
  backoffMs: 500,
  sessionTimeoutMs: 30 * 60 * 1000,
};

function detectDevice(): Device {
  if (typeof navigator === "undefined" || !navigator.userAgent) return "desktop";
  const ua = navigator.userAgent.toLowerCase();
  if (/ipad|tablet/.test(ua) && !/mobile/.test(ua)) return "tablet";
  if (/mobi|iphone|android/.test(ua)) return "mobile";
  return "desktop";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Browser tracking SDK. track(eventType, payload) builds one canonical event (same field names
 * as biq/schema.py's Event), buffers it, and flushes the buffer every flushIntervalMs or once
 * batchSize events have queued - whichever comes first - plus immediately via
 * navigator.sendBeacon when the page is hidden/unloaded, so a closing tab never loses its tail.
 */
export class BehaviorTracker {
  private readonly identity: IdentityStore;
  private readonly ingestUrl: string;
  private readonly flushIntervalMs: number;
  private readonly batchSize: number;
  private readonly maxRetries: number;
  private readonly backoffMs: number;
  private readonly fetchImpl: typeof fetch;
  private readonly now: () => number;

  private buffer: TrackedEvent[] = [];
  private scrollDepth = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private onScroll?: () => void;
  private onPageHide?: () => void;
  private onVisibilityChange?: () => void;

  constructor(options: BehaviorTrackerOptions) {
    this.ingestUrl = options.ingestUrl;
    this.flushIntervalMs = options.flushIntervalMs ?? DEFAULTS.flushIntervalMs;
    this.batchSize = options.batchSize ?? DEFAULTS.batchSize;
    this.maxRetries = options.maxRetries ?? DEFAULTS.maxRetries;
    this.backoffMs = options.backoffMs ?? DEFAULTS.backoffMs;
    // fetch is a "this"-checking native method - an unbound reference throws "Illegal invocation"
    // the moment it's actually called, which sendWithRetry's try/catch swallows into silent,
    // repeated, ultimately-abandoned retries. Bind it to the global object that owns it.
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
    this.now = options.now ?? (() => Date.now());

    const sessionTimeoutMs = options.sessionTimeoutMs ?? DEFAULTS.sessionTimeoutMs;
    const storage = options.storage ?? (typeof localStorage !== "undefined" ? localStorage : memoryStorage());
    this.identity = new IdentityStore(storage, sessionTimeoutMs);

    if (typeof window !== "undefined") {
      this.onScroll = () => this.updateScrollDepth();
      window.addEventListener("scroll", this.onScroll, { passive: true });
      this.onPageHide = () => void this.flush(true);
      window.addEventListener("pagehide", this.onPageHide);
      this.onVisibilityChange = () => {
        if (document.visibilityState === "hidden") void this.flush(true);
      };
      document.addEventListener("visibilitychange", this.onVisibilityChange);
      this.timer = setInterval(() => void this.flush(false), this.flushIntervalMs);
    }
  }

  track(eventType: EventType, payload: TrackPayload = {}): TrackedEvent {
    const now = this.now();
    const session = this.identity.getSession(now);
    const seq = session.seq;
    const dwellMs = Math.max(0, Math.round(now - session.last_event_at));

    this.identity.save({
      session_id: session.session_id,
      seq: seq + 1,
      last_activity: now,
      last_event_at: now,
    });

    const event: TrackedEvent = {
      event_id: uuidv4(),
      session_id: session.session_id,
      anon_user_id: this.identity.getAnonUserId(),
      event_type: eventType,
      timestamp: new Date(now).toISOString(),
      page: payload.page ?? (typeof location !== "undefined" ? location.pathname : "/"),
      product_id: payload.product_id ?? null,
      category: payload.category ?? null,
      device: payload.device ?? detectDevice(),
      referrer: payload.referrer ?? (typeof document !== "undefined" && document.referrer ? document.referrer : "direct"),
      meta: { scroll_depth: this.scrollDepth, dwell_ms: dwellMs, seq },
    };

    this.buffer.push(event);
    if (this.buffer.length >= this.batchSize) {
      void this.flush(false);
    }
    return event;
  }

  private updateScrollDepth(): void {
    if (typeof document === "undefined") return;
    const doc = document.documentElement;
    const scrollable = doc.scrollHeight - doc.clientHeight;
    this.scrollDepth = scrollable > 0 ? Math.min(1, Math.max(0, window.scrollY / scrollable)) : 0;
  }

  /** Sends whatever is buffered. useBeacon=true is for page-hide/unload (fire-and-forget, no retry). */
  async flush(useBeacon = false): Promise<void> {
    if (this.buffer.length === 0) return;
    const batch = this.buffer;
    this.buffer = [];

    if (useBeacon && typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      const blob = new Blob([JSON.stringify(batch)], { type: "application/json" });
      const queued = navigator.sendBeacon(this.ingestUrl, blob);
      if (!queued) this.buffer.unshift(...batch); // browser's beacon queue was full - retry later
      return;
    }

    const ok = await this.sendWithRetry(batch);
    if (!ok) this.buffer.unshift(...batch); // keep events for the next flush instead of dropping them
  }

  private async sendWithRetry(batch: TrackedEvent[]): Promise<boolean> {
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const res = await this.fetchImpl(this.ingestUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(batch),
          keepalive: true,
        });
        if (res.ok) return true;
      } catch {
        // network error - fall through to backoff and retry
      }
      if (attempt < this.maxRetries) {
        await sleep(this.backoffMs * 2 ** attempt);
      }
    }
    return false;
  }

  destroy(): void {
    if (this.timer) clearInterval(this.timer);
    if (typeof window !== "undefined") {
      if (this.onScroll) window.removeEventListener("scroll", this.onScroll);
      if (this.onPageHide) window.removeEventListener("pagehide", this.onPageHide);
      if (this.onVisibilityChange) document.removeEventListener("visibilitychange", this.onVisibilityChange);
    }
  }
}
