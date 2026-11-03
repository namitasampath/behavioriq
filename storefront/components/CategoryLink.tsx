"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { getTracker } from "@/lib/tracker";

export default function CategoryLink({
  category,
  page,
  className,
  children,
}: {
  category: string;
  page: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Link
      href={`/listing?category=${category}`}
      onClick={() => getTracker().track("click", { page, category })}
      className={className}
    >
      {children}
    </Link>
  );
}
