import { getCatalogue, displayName } from "@/lib/catalogue";
import ProductViewTracker from "@/components/ProductViewTracker";
import ScrollTracker from "@/components/ScrollTracker";

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ ids?: string }>;
}) {
  const { ids = "" } = await searchParams;
  const wanted = ids.split(",").filter(Boolean);
  const catalogue = getCatalogue();
  const products = wanted.map((id) => catalogue.find((p) => p.product_id === id)).filter((p): p is NonNullable<typeof p> => !!p);

  return (
    <div className="flex flex-col gap-6">
      <ScrollTracker page="/compare" />
      {products[0] && (
        <ProductViewTracker eventType="compare" page="/compare" productId={products[0].product_id} category={products[0].category} />
      )}

      <h1 className="text-2xl font-semibold">Compare</h1>

      {products.length === 0 ? (
        <p className="text-sm text-black/60 dark:text-white/60">
          No products selected yet - open a product and click &quot;Add to compare&quot;.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-black/10 dark:border-white/15">
                <th className="py-2 pr-4">Product</th>
                <th className="py-2 pr-4">Category</th>
                <th className="py-2 pr-4">Price</th>
                <th className="py-2 pr-4">Rating</th>
                <th className="py-2 pr-4">Reviews</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.product_id} className="border-b border-black/5 dark:border-white/10">
                  <td className="py-2 pr-4">{displayName(p)}</td>
                  <td className="py-2 pr-4 capitalize">{p.category}</td>
                  <td className="py-2 pr-4">${p.price.toFixed(2)}</td>
                  <td className="py-2 pr-4">{p.rating.toFixed(1)}★</td>
                  <td className="py-2 pr-4">{p.review_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
