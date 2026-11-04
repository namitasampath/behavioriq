"use client";

import { useScrollTracking } from "@/lib/useScrollTracking";

/** Thin client boundary so server-component pages can opt into scroll tracking. */
export default function ScrollTracker({ page }: { page: string }) {
  useScrollTracking(page);
  return null;
}
