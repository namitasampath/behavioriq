"use client";

import Link from "next/link";
import { useRef } from "react";
import type { Product } from "@/lib/productDisplay";
import { displayName } from "@/lib/productDisplay";
import { getTracker } from "@/lib/tracker";

export default function ProductCard({ product, page }: { product: Product; page: string }) {
  const hovered = useRef(false);

  function onHover() {
    if (hovered.current) return; // one hover event per card per page view, not one per pixel
    hovered.current = true;
    getTracker().track("hover", { page, product_id: product.product_id, category: product.category });
  }

  function onClick() {
    getTracker().track("click", { page, product_id: product.product_id, category: product.category });
  }

  return (
    <Link
      href={`/product/${product.product_id}`}
      onMouseEnter={onHover}
      onClick={onClick}
      className="flex flex-col gap-2 rounded-lg border border-black/10 p-4 transition hover:border-black/30 dark:border-white/15 dark:hover:border-white/40"
    >
      <div
        className="flex h-28 items-center justify-center rounded text-xs font-medium uppercase tracking-wide text-white"
        style={{ background: categoryColor(product.category) }}
      >
        {product.category}
      </div>
      <div className="text-sm font-medium">{displayName(product)}</div>
      <div className="flex items-center justify-between text-sm text-black/60 dark:text-white/60">
        <span>${product.price.toFixed(2)}</span>
        <span>
          {product.rating.toFixed(1)}★ ({product.review_count})
        </span>
      </div>
    </Link>
  );
}

function categoryColor(category: string): string {
  const palette: Record<string, string> = {
    electronics: "#2563eb",
    fashion: "#db2777",
    home: "#16a34a",
    beauty: "#c026d3",
    sports: "#ea580c",
    books: "#4338ca",
  };
  return palette[category] ?? "#525252";
}
