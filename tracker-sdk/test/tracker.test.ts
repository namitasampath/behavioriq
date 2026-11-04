import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BehaviorTracker } from "../src/tracker";
import { memoryStorage } from "../src/storage";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

// happy-dom's `document`/`window` are shared across tests in this file, so a tracker's
// window/document listeners (scroll, pagehide, visibilitychange) must be torn down after every
// test - otherwise earlier tests' trackers keep firing on later tests' events.
const liveTrackers: BehaviorTracker[] = [];
afterEach(() => {
  liveTrackers.forEach((t) => t.destroy());
  liveTrackers.length = 0;
});

function makeTracker(overrides: Partial<ConstructorParameters<typeof BehaviorTracker>[0]> = {}) {
  const fetchImpl = vi.fn().mockResolvedValue({ ok: true } as Response);
  let clock = 1_700_000_000_000;
  const now = vi.fn(() => clock);
  const tracker = new BehaviorTracker({
    ingestUrl: "http://test.local/track",
    storage: memoryStorage(),
    fetchImpl,
    now,
    ...overrides,
  });
  liveTrackers.push(tracker);
  return { tracker, fetchImpl, advance: (ms: number) => (clock += ms) };
}

describe("BehaviorTracker.track()", () => {
  it("generates a uuid v4 event_id, unique per call", () => {
    const { tracker } = makeTracker();
    const a = tracker.track("page_view");
    const b = tracker.track("page_view");
    expect(a.event_id).toMatch(UUID_V4);
    expect(b.event_id).toMatch(UUID_V4);
    expect(a.event_id).not.toBe(b.event_id);
  });

  it("reuses the same session_id and increments meta.seq across calls", () => {
    const { tracker } = makeTracker();
    const a = tracker.track("page_view");
    const b = tracker.track("product_view", { product_id: "product_1" });
    expect(b.session_id).toBe(a.session_id);
    expect(a.meta.seq).toBe(0);
    expect(b.meta.seq).toBe(1);
  });

  it("rotates session_id after 30 minutes of inactivity", () => {
    const { tracker, advance } = makeTracker();
    const a = tracker.track("page_view");
    advance(29 * 60 * 1000);
    const b = tracker.track("click");
    advance(31 * 60 * 1000);
    const c = tracker.track("page_view");
    expect(b.session_id).toBe(a.session_id);
    expect(b.meta.seq).toBe(1);
    expect(c.session_id).not.toBe(a.session_id);
    expect(c.meta.seq).toBe(0);
  });

  it("persists anon_user_id across tracker instances sharing storage", () => {
    const storage = memoryStorage();
    const t1 = new BehaviorTracker({ ingestUrl: "http://test.local/track", storage, fetchImpl: vi.fn() });
    const t2 = new BehaviorTracker({ ingestUrl: "http://test.local/track", storage, fetchImpl: vi.fn() });
    liveTrackers.push(t1, t2);
    const a = t1.track("page_view");
    const b = t2.track("page_view");
    expect(b.anon_user_id).toBe(a.anon_user_id);
    expect(b.session_id).toBe(a.session_id); // same storage -> same still-active session too
  });

  it("computes dwell_ms as the gap since the previous event in the session", () => {
    const { tracker, advance } = makeTracker();
    tracker.track("page_view");
    advance(2500);
    const b = tracker.track("product_view", { product_id: "product_1" });
    expect(b.meta.dwell_ms).toBe(2500);
  });

  it("never sends search query text - track() only reads the named payload fields", () => {
    const { tracker } = makeTracker();
    const payload = { page: "/search", query: "pregnancy test", q: "pregnancy test" } as unknown as Parameters<
      typeof tracker.track
    >[1];
    const event = tracker.track("search", payload);
    const serialized = JSON.stringify(event);
    expect(serialized).not.toContain("pregnancy test");
    expect(event.page).toBe("/search");
  });
});

describe("BehaviorTracker batching", () => {
  it("flushes automatically once batchSize events have queued", async () => {
    const { tracker, fetchImpl } = makeTracker({ batchSize: 10, flushIntervalMs: 999_999 });
    for (let i = 0; i < 9; i++) tracker.track("click");
    expect(fetchImpl).not.toHaveBeenCalled();
    tracker.track("click"); // 10th event
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    const body = JSON.parse(fetchImpl.mock.calls[0][1].body as string);
    expect(body).toHaveLength(10);
  });

  it("flushes on the timer even under batchSize", async () => {
    vi.useFakeTimers();
    try {
      const { tracker, fetchImpl } = makeTracker({ batchSize: 10, flushIntervalMs: 3000 });
      tracker.track("page_view");
      tracker.track("scroll");
      expect(fetchImpl).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(3000);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      const body = JSON.parse(fetchImpl.mock.calls[0][1].body as string);
      expect(body).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("retries with backoff and does not lose events on transient failure", async () => {
    vi.useFakeTimers();
    try {
      const fetchImpl = vi
        .fn()
        .mockRejectedValueOnce(new Error("network down"))
        .mockRejectedValueOnce(new Error("network down"))
        .mockResolvedValueOnce({ ok: true } as Response);
      const tracker = new BehaviorTracker({
        ingestUrl: "http://test.local/track",
        storage: memoryStorage(),
        fetchImpl,
        maxRetries: 3,
        backoffMs: 100,
      });
      liveTrackers.push(tracker);
      tracker.track("page_view");
      const flushPromise = tracker.flush(false);
      await vi.advanceTimersByTimeAsync(10_000);
      await flushPromise;
      expect(fetchImpl).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("re-queues the batch instead of dropping it when every retry fails", async () => {
    vi.useFakeTimers();
    try {
      const fetchImpl = vi.fn().mockRejectedValue(new Error("down"));
      const tracker = new BehaviorTracker({
        ingestUrl: "http://test.local/track",
        storage: memoryStorage(),
        fetchImpl,
        maxRetries: 1,
        backoffMs: 10,
      });
      liveTrackers.push(tracker);
      tracker.track("page_view");
      const flushPromise = tracker.flush(false);
      await vi.advanceTimersByTimeAsync(1000);
      await flushPromise;
      fetchImpl.mockResolvedValue({ ok: true } as Response);
      await tracker.flush(false);
      expect(fetchImpl).toHaveBeenCalledTimes(3); // 2 failed attempts, then 1 successful retry
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("BehaviorTracker page-hide flush", () => {
  let sendBeaconSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    sendBeaconSpy = vi.fn().mockReturnValue(true);
    // happy-dom does not implement sendBeacon
    (navigator as unknown as { sendBeacon: typeof sendBeaconSpy }).sendBeacon = sendBeaconSpy;
  });

  afterEach(() => {
    delete (navigator as unknown as { sendBeacon?: unknown }).sendBeacon;
  });

  it("uses navigator.sendBeacon when the page becomes hidden", async () => {
    const { tracker } = makeTracker();
    tracker.track("page_view");
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
    expect(sendBeaconSpy).toHaveBeenCalledTimes(1);
    expect(sendBeaconSpy.mock.calls[0][0]).toBe("http://test.local/track");
  });

  it("flushes via sendBeacon on pagehide too", async () => {
    const { tracker } = makeTracker();
    tracker.track("page_view");
    window.dispatchEvent(new Event("pagehide"));
    expect(sendBeaconSpy).toHaveBeenCalledTimes(1);
  });
});
