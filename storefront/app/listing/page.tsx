import { getCatalogue, getCategories } from "@/lib/catalogue";
import ProductCard from "@/components/ProductCard";
import ScrollTracker from "@/components/ScrollTracker";
import CategoryLink from "@/components/CategoryLink";

export default async function ListingPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const { category } = await searchParams;
  const categories = getCategories();
  const products = category ? getCatalogue().filter((p) => p.category === category) : getCatalogue();

  return (
    <div className="flex flex-col gap-6">
      <ScrollTracker page="/listing" />
      <h1 className="text-2xl font-semibold">{category ? `${category[0].toUpperCase()}${category.slice(1)}` : "All products"}</h1>

      <div className="flex flex-wrap gap-2">
        <CategoryLink
          category=""
          page="/listing"
          className={chip(!category)}
        >
          All
        </CategoryLink>
        {categories.map((c) => (
          <CategoryLink key={c} category={c} page="/listing" className={chip(category === c)}>
            {c}
          </CategoryLink>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
        {products.map((p) => (
          <ProductCard key={p.product_id} product={p} page="/listing" />
        ))}
      </div>
    </div>
  );
}

function chip(active: boolean): string {
  return `rounded-full border px-3 py-1 text-sm capitalize ${
    active ? "border-black bg-black text-white dark:border-white dark:bg-white dark:text-black" : "border-black/15 dark:border-white/20"
  }`;
}
