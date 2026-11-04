"use client";

import { BehaviorTracker } from "behavior-tracker";

let instance: BehaviorTracker | null = null;

/** One BehaviorTracker per browser tab, lazily created on first use (client-side only). */
export function getTracker(): BehaviorTracker {
  if (!instance) {
    instance = new BehaviorTracker({
      ingestUrl: process.env.NEXT_PUBLIC_INGEST_URL ?? "http://localhost:8000/track",
    });
  }
  return instance;
}
