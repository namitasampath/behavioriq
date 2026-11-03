import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import "./globals.css";
import { CartProvider } from "@/lib/cart";
import Header from "@/components/Header";
import TrackerBoot from "@/components/TrackerBoot";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "BehaviorIQ Store",
  description: "Stage 1 storefront - Next.js + the behavior-tracker SDK feeding biq/ingest_api.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <CartProvider>
          <Suspense fallback={null}>
            <TrackerBoot />
          </Suspense>
          <Header />
          <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6">{children}</main>
          <footer className="border-t border-black/10 px-4 py-4 text-center text-xs text-black/50 dark:border-white/15 dark:text-white/50">
            BehaviorIQ demo storefront - every click here becomes a behavioural event.
          </footer>
        </CartProvider>
      </body>
    </html>
  );
}
