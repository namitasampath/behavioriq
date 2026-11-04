import { searchCatalogue } from "@/lib/catalogue";
import ProductCard from "@/components/ProductCard";
import ScrollTracker from "@/components/ScrollTracker";
import SearchTracker from "@/components/SearchTracker";

export default async function SearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q = "" } = await searchParams;
  const results = searchCatalogue(q);

  return (
    <div className="flex flex-col gap-6">
      <ScrollTracker page="/search" />
      <SearchTracker query={q} />
      <h1 className="text-2xl font-semibold">Search</h1>
      <p className="text-sm text-black/60 dark:text-white/60">
        {results.length} result{results.length === 1 ? "" : "s"} - try a category name like &quot;electronics&quot; or
        &quot;fashion&quot;.
      </p>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
        {results.map((p) => (
          <ProductCard key={p.product_id} product={p} page="/search" />
        ))}
      </div>
    </div>
  );
}
