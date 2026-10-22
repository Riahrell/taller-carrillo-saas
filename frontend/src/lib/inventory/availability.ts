import { getJson, type RequestOptions } from "@/lib/api/client";
import type { Category, Page } from "@/lib/catalog/api";

export type AvailableProduct = {
  id: string;
  name: string;
  sku: string | null;
  category: string | null;
  price: string;
  quantity: string;
};
export type AvailabilityQuery = {
  q: string;
  category: string | null;
  after?: string;
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value))
    throw new Error("Invalid ID");
  return value;
}
function object(value: unknown, fields: string[]): Record<string, unknown> {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(",") !== [...fields].sort().join(",")
  )
    throw new Error("Invalid fields");
  return value as Record<string, unknown>;
}
function name(value: unknown, max = 200): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    Array.from(value).length > max ||
    !value.isWellFormed() ||
    value.includes("\0")
  )
    throw new Error("Invalid name");
  return value;
}
function product(value: unknown): AvailableProduct {
  const row = object(value, [
    "id",
    "name",
    "sku",
    "category",
    "price",
    "quantity",
  ]);
  if (
    typeof row.price !== "string" ||
    !/^(0|[1-9][0-9]{0,17})\.[0-9]{2}$/.test(row.price) ||
    typeof row.quantity !== "string" ||
    !/^(0|[1-9][0-9]{0,14})\.[0-9]{3}$/.test(row.quantity)
  )
    throw new Error("Invalid decimals");
  return {
    id: id(row.id),
    name: name(row.name),
    sku: row.sku === null ? null : name(row.sku, 100),
    category: row.category === null ? null : name(row.category),
    price: row.price,
    quantity: row.quantity,
  };
}
function page<T extends { id: string }>(
  value: unknown,
  decode: (v: unknown) => T,
  after?: string,
): Page<T> {
  const row = object(value, ["items", "next_after"]);
  if (!Array.isArray(row.items) || row.items.length > 25)
    throw new Error("Invalid page");
  const items = row.items.map(decode);
  if (
    items.some(
      (item, index) => item.id <= (index ? items[index - 1].id : (after ?? "")),
    )
  )
    throw new Error("Invalid order");
  const next = row.next_after === null ? null : id(row.next_after);
  if (next !== null && (items.length !== 25 || items.at(-1)?.id !== next))
    throw new Error("Invalid cursor");
  return { items, next_after: next };
}
function bound(actor: string, headers: Headers, status: number) {
  if (status !== 200 || headers.get("X-Access-Membership") !== id(actor))
    throw new Error("Context changed");
}
export const availabilityApi = {
  products(
    actor: string,
    query: AvailabilityQuery,
    options: RequestOptions = {},
  ) {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.category) params.set("category_id", id(query.category));
    if (query.after) params.set("after", id(query.after));
    return getJson(
      `/inventory/availability${params.size ? `?${params}` : ""}`,
      (v, s, h) => {
        bound(actor, h, s);
        return page(v, product, query.after);
      },
      options,
    );
  },
  categories(
    actor: string,
    after?: string,
    options: RequestOptions = {},
  ): Promise<Page<Category>> {
    return getJson(
      `/inventory/availability/categories${after ? `?after=${id(after)}` : ""}`,
      (v, s, h) => {
        bound(actor, h, s);
        return page(
          v,
          (value) => {
            const row = object(value, ["id", "name"]);
            return { id: id(row.id), name: name(row.name), is_active: true };
          },
          after,
        );
      },
      options,
    );
  },
};
