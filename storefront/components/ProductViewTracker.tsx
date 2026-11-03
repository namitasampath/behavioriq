"use client";

import { useEffect, useRef } from "react";
import { getTracker } from "@/lib/tracker";
import type { EventType } from "behavior-tracker";

/** Fires one vocabulary event (product_view / review_view / compare, ...) once per product per mount. */
export default function ProductViewTracker({
  eventType,
  page,
  productId,
  category,
}: {
  eventType: EventType;
  page: string;
  productId: string;
  category: string;
}) {
  const fired = useRef<string | null>(null);

  useEffect(() => {
    const key = `${eventType}:${productId}`;
    if (fired.current === key) return;
    fired.current = key;
    getTracker().track(eventType, { page, product_id: productId, category });
  }, [eventType, page, productId, category]);

  return null;
}
