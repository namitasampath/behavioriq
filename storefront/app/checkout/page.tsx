"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useCart } from "@/lib/cart";
import { getTracker } from "@/lib/tracker";
import ScrollTracker from "@/components/ScrollTracker";

export default function CheckoutPage() {
  const { items, total, clear } = useCart();
  const router = useRouter();
  const fired = useRef(false);
  const [email, setEmail] = useState("");

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    getTracker().track("checkout_start", { page: "/checkout" });
  }, []);

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    clear();
    router.push("/confirmation");
  }

  return (
    <div className="flex flex-col gap-6">
      <ScrollTracker page="/checkout" />
      <h1 className="text-2xl font-semibold">Checkout</h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        {items.length} item{items.length === 1 ? "" : "s"} · Total ${total.toFixed(2)}
      </p>

      <form onSubmit={onSubmit} className="flex max-w-sm flex-col gap-3">
        <label className="text-sm">
          Email
          <input
            required
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded border border-black/15 px-3 py-2 text-sm dark:border-white/20 dark:bg-transparent"
          />
        </label>
        <label className="text-sm">
          Card number (demo only, not sent anywhere)
          <input
            required
            placeholder="4242 4242 4242 4242"
            className="mt-1 w-full rounded border border-black/15 px-3 py-2 text-sm dark:border-white/20 dark:bg-transparent"
          />
        </label>
        <button type="submit" className="mt-2 rounded bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black">
          Place order
        </button>
      </form>
    </div>
  );
}
