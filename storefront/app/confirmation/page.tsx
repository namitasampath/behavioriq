"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getTracker } from "@/lib/tracker";

export default function ConfirmationPage() {
  const fired = useRef(false);
  const [orderId] = useState(() => `ORD-${Math.floor(Math.random() * 1_000_000)}`);

  useEffect(() => {
    if (fired.current) return;
    fired.current = true;
    // Matches biq/simulator.py's convention: the purchase event's page is "/confirmation",
    // regardless of which page in the funnel actually triggered it.
    getTracker().track("purchase", { page: "/confirmation" });
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Thank you!</h1>
      <p className="text-sm text-black/60 dark:text-white/60">Order {orderId} is confirmed.</p>
      <Link href="/" className="text-sm underline">
        Back to home
      </Link>
    </div>
  );
}
