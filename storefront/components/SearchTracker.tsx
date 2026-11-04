"use client";

import { useEffect, useRef } from "react";
import { getTracker } from "@/lib/tracker";

/**
 * Fires one "search" event per distinct query. `query` is read only to detect "this is a new
 * search" - it is never passed into track(), so it can never reach the tracked event or the
 * network, per tracker-sdk's contract (there is no field for it in biq/schema.py anyway).
 */
export default function SearchTracker({ query }: { query: string }) {
  const lastFired = useRef<string | null>(null);

  useEffect(() => {
    if (lastFired.current === query) return;
    lastFired.current = query;
    getTracker().track("search", { page: "/search" });
  }, [query]);

  return null;
}
