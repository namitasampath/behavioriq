"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { useCart } from "@/lib/cart";
import { getTracker } from "@/lib/tracker";

export default function Header() {
  const router = useRouter();
  const { count } = useCart();
  const [q, setQ] = useState("");

  function onSearch(e: FormEvent) {
    e.preventDefault();
    // Only the destination path is tracked here (via TrackerBoot's page_view on navigate) - the
    // query text itself is never attached to a tracked event, per tracker-sdk's contract.
    router.push(`/search?q=${encodeURIComponent(q)}`);
  }

  function onCategoryClick(category: string) {
    getTracker().track("click", { page: "/", category });
  }

  return (
    <header className="border-b border-black/10 dark:border-white/15">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-4 px-4 py-3">
        <Link href="/" className="text-lg font-semibold tracking-tight">
          BehaviorIQ Store
        </Link>
        <nav className="flex gap-3 text-sm">
          <Link href="/listing" className="hover:underline" onClick={() => onCategoryClick("")}>
            Shop
          </Link>
          <Link href="/compare" className="hover:underline">
            Compare
          </Link>
        </nav>
        <form onSubmit={onSearch} className="ml-auto flex items-center gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search products or categories"
            className="w-56 rounded border border-black/15 px-3 py-1.5 text-sm dark:border-white/20 dark:bg-transparent"
          />
          <button type="submit" className="rounded bg-black px-3 py-1.5 text-sm text-white dark:bg-white dark:text-black">
            Search
          </button>
        </form>
        <Link href="/cart" className="rounded border border-black/15 px-3 py-1.5 text-sm dark:border-white/20">
          Cart ({count})
        </Link>
      </div>
    </header>
  );
}
