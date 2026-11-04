"use client";

import { useEffect, useRef } from "react";
import { getTracker } from "./tracker";

/**
 * Fires one "scroll" vocabulary event per 25%-of-page threshold crossed, instead of on every
 * scroll tick - meta.scroll_depth (attached to every event) already carries the continuous value.
 */
export function useScrollTracking(page: string): void {
  const lastBucket = useRef(0);

  useEffect(() => {
    lastBucket.current = 0;
    const onScroll = () => {
      const doc = document.documentElement;
      const scrollable = doc.scrollHeight - doc.clientHeight;
      const depth = scrollable > 0 ? Math.min(1, Math.max(0, window.scrollY / scrollable)) : 0;
      const bucket = Math.floor(depth * 4); // 0,1,2,3,4 -> 0%,25%,50%,75%,100%
      if (bucket > lastBucket.current) {
        lastBucket.current = bucket;
        getTracker().track("scroll", { page });
      }
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [page]);
}
