"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Product } from "@/lib/catalogue";
import { useCart } from "@/lib/cart";
import { getTracker } from "@/lib/tracker";
import { addCompareId } from "@/lib/compare";

export default function ProductActions({ product, page }: { product: Product; page: string }) {
  const { add } = useCart();
  const router = useRouter();
  const [added, setAdded] = useState(false);

  function onAddToCart() {
    add({ product_id: product.product_id, category: product.category, price: product.price });
    getTracker().track("add_to_cart", { page, product_id: product.product_id, category: product.category });
    setAdded(true);
  }

  function onCompare() {
    const ids = addCompareId(product.product_id);
    router.push(`/compare?ids=${ids.join(",")}`);
  }

  return (
    <div className="flex gap-3">
      <button
        onClick={onAddToCart}
        className="rounded bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
      >
        {added ? "Added ✓" : "Add to cart"}
      </button>
      <button onClick={onCompare} className="rounded border border-black/20 px-4 py-2 text-sm dark:border-white/25">
        Add to compare
      </button>
    </div>
  );
}
