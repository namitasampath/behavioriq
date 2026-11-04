"use client";

import Link from "next/link";
import { useCart } from "@/lib/cart";
import { getTracker } from "@/lib/tracker";
import ScrollTracker from "@/components/ScrollTracker";

export default function CartPage() {
  const { items, total, remove } = useCart();

  function onRemove(productId: string, category: string) {
    remove(productId);
    getTracker().track("remove_from_cart", { page: "/cart", product_id: productId, category });
  }

  return (
    <div className="flex flex-col gap-6">
      <ScrollTracker page="/cart" />
      <h1 className="text-2xl font-semibold">Your cart</h1>

      {items.length === 0 ? (
        <p className="text-sm text-black/60 dark:text-white/60">
          Your cart is empty. <Link href="/listing" className="underline">Keep shopping</Link>.
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-3">
            {items.map((item) => (
              <li
                key={item.product_id}
                className="flex items-center justify-between rounded-lg border border-black/10 p-3 dark:border-white/15"
              >
                <div>
                  <div className="text-sm font-medium capitalize">{item.category} item - {item.product_id}</div>
                  <div className="text-sm text-black/60 dark:text-white/60">
                    ${item.price.toFixed(2)} × {item.qty}
                  </div>
                </div>
                <button onClick={() => onRemove(item.product_id, item.category)} className="text-sm text-red-600 underline">
                  Remove
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between border-t border-black/10 pt-4 dark:border-white/15">
            <span className="text-lg font-semibold">Total: ${total.toFixed(2)}</span>
            <Link href="/checkout" className="rounded bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black">
              Checkout
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
