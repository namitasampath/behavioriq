import { readFileSync } from "node:fs";
import path from "node:path";
import type { Product } from "./productDisplay";

// Server-only: reads the same data/catalogue.json the rest of BehaviorIQ uses (biq/simulator.py's
// make_catalogue output), one level up from this app, instead of duplicating product data here.
// Client components must import displayName/Product from ./productDisplay instead of here, or
// this module's node:fs/node:path imports get pulled into the browser bundle.

export type { Product };
export { displayName } from "./productDisplay";

let cache: Product[] | null = null;

export function getCatalogue(): Product[] {
  if (!cache) {
    const file = path.join(process.cwd(), "..", "data", "catalogue.json");
    cache = JSON.parse(readFileSync(file, "utf-8")) as Product[];
  }
  return cache;
}

export function getCategories(): string[] {
  return Array.from(new Set(getCatalogue().map((p) => p.category))).sort();
}

export function getProduct(id: string): Product | undefined {
  return getCatalogue().find((p) => p.product_id === id);
}

export function getRelated(product: Product, limit = 4): Product[] {
  return getCatalogue()
    .filter((p) => p.category === product.category && p.product_id !== product.product_id)
    .slice(0, limit);
}

export function searchCatalogue(query: string): Product[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return getCatalogue().filter((p) => p.category.toLowerCase().includes(q) || p.product_id.toLowerCase().includes(q));
}
