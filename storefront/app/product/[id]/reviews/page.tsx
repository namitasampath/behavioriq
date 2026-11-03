import { notFound } from "next/navigation";
import Link from "next/link";
import { getProduct, displayName } from "@/lib/catalogue";
import ProductViewTracker from "@/components/ProductViewTracker";
import ScrollTracker from "@/components/ScrollTracker";

const BLURBS = [
  "Does exactly what it says. Would buy again.",
  "Good value for the price, shipping was fast.",
  "Quality is fine but not amazing - matches the rating.",
  "Better than expected for this category.",
  "Solid everyday option, no complaints so far.",
];

export default async function ReviewsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const product = getProduct(id);
  if (!product) notFound();
  const page = `/product/${id}/reviews`;

  // Deterministic placeholder reviews derived from the product's own rating/review_count - the
  // catalogue (biq/simulator.py's make_catalogue) carries no real review text.
  const seed = hashCode(product.product_id);
  const reviews = Array.from({ length: 3 }, (_, i) => ({
    stars: Math.max(1, Math.min(5, Math.round(product.rating + (((seed + i) % 3) - 1)))),
    text: BLURBS[(seed + i) % BLURBS.length],
  }));

  return (
    <div className="flex flex-col gap-6">
      <ScrollTracker page={page} />
      <ProductViewTracker eventType="review_view" page={page} productId={product.product_id} category={product.category} />

      <Link href={`/product/${id}`} className="text-sm underline">
        ← back to {displayName(product)}
      </Link>
      <h1 className="text-2xl font-semibold">Reviews - {displayName(product)}</h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        {product.rating.toFixed(1)}★ average across {product.review_count} reviews
      </p>

      <ul className="flex flex-col gap-3">
        {reviews.map((r, i) => (
          <li key={i} className="rounded-lg border border-black/10 p-4 dark:border-white/15">
            <div className="text-sm">{"★".repeat(r.stars)}{"☆".repeat(5 - r.stars)}</div>
            <p className="mt-1 text-sm text-black/70 dark:text-white/70">{r.text}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function hashCode(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}
