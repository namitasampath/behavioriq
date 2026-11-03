// Pure, no I/O - safe to import from client components too (unlike lib/catalogue.ts, which
// reads the filesystem and must stay server-only).

export interface Product {
  product_id: string;
  category: string;
  price: number;
  rating: number;
  review_count: number;
}

/** The catalogue has no title field (biq/simulator.py never needed one) - derive a readable label. */
export function displayName(product: Product): string {
  const n = product.product_id.split("_")[1] ?? product.product_id;
  const label = product.category.charAt(0).toUpperCase() + product.category.slice(1);
  return `${label} Item #${n}`;
}
