import { getCatalogue, getCategories } from "@/lib/catalogue";
import ProductCard from "@/components/ProductCard";
import ScrollTracker from "@/components/ScrollTracker";
import CategoryLink from "@/components/CategoryLink";

export default function HomePage() {
  const categories = getCategories();
  const featured = getCatalogue().slice(0, 8);

  return (
    <div className="flex flex-col gap-8">
      <ScrollTracker page="/" />
      <section>
        <h1 className="text-2xl font-semibold">Welcome to BehaviorIQ Store</h1>
        <p className="mt-1 text-sm text-black/60 dark:text-white/60">
          A small storefront that fires a real behavioural event for everything you do on it.
        </p>
      </section>

      <section className="flex flex-wrap gap-2">
        {categories.map((c) => (
          <CategoryLink
            key={c}
            category={c}
            page="/"
            className="rounded-full border border-black/15 px-3 py-1 text-sm capitalize hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
          >
            {c}
          </CategoryLink>
        ))}
      </section>

      <section>
        <h2 className="mb-3 text-lg font-medium">Featured</h2>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
          {featured.map((p) => (
            <ProductCard key={p.product_id} product={p} page="/" />
          ))}
        </div>
      </section>
    </div>
  );
}
