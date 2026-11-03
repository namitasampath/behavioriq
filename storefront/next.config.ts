import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Every page here depends on request-time state (cart, session) - plain dynamic SSR (Cache
  // Components/PPR left off) keeps that simple instead of needing "use cache"/Suspense boundaries.
  // Build/dev run on webpack (package.json's --webpack flag): Turbopack's resolver can't find the
  // file:-linked local tracker-sdk/ package ("Module not found: Can't resolve 'behavior-tracker'",
  // confirmed with plain `next build` before this was added); webpack resolves it fine via
  // transpilePackages below.
  transpilePackages: ["behavior-tracker"], // tracker-sdk/ ships raw TypeScript, no build step
};

export default nextConfig;
