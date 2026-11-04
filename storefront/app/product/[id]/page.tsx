import { notFound } from "next/navigation";
import Link from "next/link";
import { getProduct, getRelated, displayName } from "@/lib/catalogue";
import ProductCard from "@/components/ProductCard";
import ProductActions from "@/components/ProductActions";
import ProductViewTracker from "@/components/ProductViewTracker";
import ScrollTracker from "@/components/ScrollTracker";

export default async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const product = getProduct(id);
  if (!product) notFound();
  const related = getRelated(product);
  const page = `/product/${id}`;

  return (
    <div className="flex flex-col gap-8">
      <ScrollTracker page={page} />
      <ProductViewTracker eventType="product_view" page={page} productId={product.product_id} category={product.category} />

      <div className="grid gap-6 sm:grid-cols-2">
        <div
          className="flex h-64 items-center justify-center rounded-lg text-sm font-medium uppercase tracking-wide text-white"
          style={{ background: "#404040" }}
        >
          {product.category}
        </div>
        <div className="flex flex-col gap-3">
          <h1 className="text-2xl font-semibold">{displayName(product)}</h1>
          <p className="text-sm capitalize text-black/60 dark:text-white/60">{product.category}</p>
          <p className="text-xl font-semibold">${product.price.toFixed(2)}</p>
          <p className="text-sm text-black/60 dark:text-white/60">
            {product.rating.toFixed(1)}★ ({product.review_count} reviews) ·{" "}
            <Link href={`${page}/reviews`} className="underline">
              read reviews
            </Link>
          </p>
          <ProductActions product={product} page={page} />
        </div>
      </div>

      {related.length > 0 && (
        <section>
          <h2 className="mb-3 text-lg font-medium">You might also like</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {related.map((p) => (
              <ProductCard key={p.product_id} product={p} page={page} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
