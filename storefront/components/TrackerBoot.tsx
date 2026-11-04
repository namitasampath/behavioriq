"use client";

import { useEffect } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { getTracker } from "@/lib/tracker";

/** Mounted once in the root layout: fires page_view on every route change, and exit on page hide. */
export default function TrackerBoot() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    const qs = searchParams.toString();
    getTracker().track("page_view", { page: qs ? `${pathname}?${qs}` : pathname });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname, searchParams]);

  useEffect(() => {
    const fireExit = () => {
      const tracker = getTracker();
      tracker.track("exit", { page: pathname });
      void tracker.flush(true);
    };
    const onVisibility = () => {
      if (document.visibilityState === "hidden") fireExit();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", fireExit);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", fireExit);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return null;
}
