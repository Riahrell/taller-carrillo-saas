import { accessApi, readCsrfCookie } from "@/lib/access/api";
import {
  createJson,
  getJson,
  patchJson,
  type RequestOptions,
} from "@/lib/api/client";

export type Category = { id: string; name: string; is_active: boolean };
export type Product = Category & {
  category_id: string | null;
  sku: string | null;
  price: string;
};
export type Page<T> = { items: T[]; next_after: string | null };
export type NewProduct = {
  name: string;
  price: string;
  category_id: string | null;
  sku: string | null;
};
export type VersionedProduct = { product: Product; etag: string };
export type ProductChange = NewProduct & { is_active: boolean };
function productEtag(value: string | null, productId: string): string {
  if (
    value === null ||
    !new RegExp(`^"product-v1-${id(productId)}-(0|[1-9][0-9]{0,18})"$`).test(
      value,
    )
  )
    throw new Error("Invalid product version");
  return value;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PRICE = /^(0|[1-9][0-9]{0,17})(?:\.([0-9]{1,2}))?$/;
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value))
    throw new Error("Invalid ID");
  return value;
}
function record(value: unknown, fields: string[]): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("Invalid data");
  if (Object.keys(value).sort().join(",") !== [...fields].sort().join(","))
    throw new Error("Unexpected fields");
  return value as Record<string, unknown>;
}
function name(value: unknown, maximum = 200): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    Array.from(value).length > maximum ||
    !value.isWellFormed() ||
    value.includes("\0")
  )
    throw new Error("Invalid name");
  return value;
}
export function priceInput(value: string): string {
  const match = PRICE.exec(value.trim().replace(",", "."));
  if (!match) throw new Error("Invalid price");
  return `${match[1]}.${(match[2] ?? "").padEnd(2, "0")}`;
}
export function displayPrice(value: string): string {
  const [whole, fraction] = priceInput(value).split(".");
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${fraction} USD`;
}
function bound(
  headers: Headers,
  actor: string,
  status: number,
  expected = 200,
) {
  if (status !== expected || headers.get("X-Access-Membership") !== id(actor))
    throw new Error("Unmatched context");
}
export function decodeCategory(value: unknown): Category {
  const data = record(value, ["id", "name", "is_active"]);
  if (typeof data.is_active !== "boolean") throw new Error("Invalid state");
  return { id: id(data.id), name: name(data.name), is_active: data.is_active };
}
export function decodeProduct(value: unknown): Product {
  const data = record(value, [
    "id",
    "name",
    "is_active",
    "category_id",
    "sku",
    "price",
  ]);
  if (
    typeof data.is_active !== "boolean" ||
    typeof data.price !== "string" ||
    !PRICE.test(data.price)
  )
    throw new Error("Invalid product");
  return {
    id: id(data.id),
    name: name(data.name),
    is_active: data.is_active,
    category_id: data.category_id === null ? null : id(data.category_id),
    sku: data.sku === null ? null : name(data.sku, 100),
    price: priceInput(data.price),
  };
}
export function decodePage<T extends Category>(
  value: unknown,
  status: number,
  headers: Headers,
  actor: string,
  decode: (value: unknown) => T,
  after?: string,
): Page<T> {
  bound(headers, actor, status);
  const data = record(value, ["items", "next_after"]);
  if (!Array.isArray(data.items) || data.items.length > 25)
    throw new Error("Invalid page");
  const items = data.items.map(decode);
  if (
    items.some(
      (item, index) => item.id <= (index ? items[index - 1].id : (after ?? "")),
    )
  )
    throw new Error("Invalid order");
  const next = data.next_after === null ? null : id(data.next_after);
  if (next !== null && (items.length !== 25 || next !== items.at(-1)?.id))
    throw new Error("Invalid cursor");
  return { items, next_after: next };
}
async function create<T>(
  kind: "products" | "categories",
  actor: string,
  data: unknown,
  decode: (value: unknown) => T,
  options: RequestOptions,
): Promise<T> {
  id(actor);
  await accessApi.prepare(options);
  const csrf = readCsrfCookie(
    document.cookie,
    window.location.protocol === "https:",
  );
  return createJson(
    `/catalog/${kind}`,
    data,
    csrf,
    actor,
    (value, status, headers) => {
      bound(headers, actor, status, 201);
      return decode(value);
    },
    options,
  );
}
export function productSearchInput(value: string): string {
  const text = value.trim();
  if (
    Array.from(text).length > 200 ||
    !text.isWellFormed() ||
    text.includes("\0")
  )
    throw new Error("Invalid search");
  return text;
}
function searchQuery(value: string): string {
  const text = productSearchInput(value);
  return text ? `&q=${encodeURIComponent(text)}` : "";
}
export const catalogApi = {
  editable: (
    actor: string,
    target: string,
    options: RequestOptions = {},
  ): Promise<VersionedProduct> =>
    getJson(
      `/catalog/products/${id(target)}`,
      (value, status, headers) => {
        bound(headers, actor, status);
        const product = decodeProduct(value);
        if (product.id !== target) throw new Error("Unmatched product");
        return { product, etag: productEtag(headers.get("ETag"), target) };
      },
      options,
    ),
  updateProduct: async (
    actor: string,
    target: string,
    version: string,
    raw: ProductChange,
    options: RequestOptions = {},
  ): Promise<VersionedProduct> => {
    id(actor);
    id(target);
    const etag = productEtag(version, target);
    if (typeof raw.is_active !== "boolean") throw new Error("Invalid state");
    const data = {
      name: name(raw.name.trim()),
      price: priceInput(raw.price),
      sku: raw.sku === null ? null : name(raw.sku.trim(), 100),
      category_id: raw.category_id === null ? null : id(raw.category_id),
      is_active: raw.is_active,
    };
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      window.location.protocol === "https:",
    );
    return patchJson(
      `/catalog/products/${target}`,
      data,
      csrf,
      etag,
      actor,
      (value, status, headers) => {
        bound(headers, actor, status);
        const product = decodeProduct(value);
        if (
          product.id !== target ||
          product.name !== data.name ||
          product.price !== data.price ||
          product.sku !== data.sku ||
          product.category_id !== data.category_id ||
          product.is_active !== data.is_active
        )
          throw new Error("Changed product");
        return { product, etag: productEtag(headers.get("ETag"), target) };
      },
      options,
    );
  },
  products: (
    actor: string,
    after?: string,
    options: RequestOptions = {},
    search = "",
    category: string | null = null,
  ) =>
    getJson(
      `/catalog/products?limit=25${after ? `&after=${id(after)}` : ""}${searchQuery(search)}${category === null ? "" : `&category_id=${id(category)}`}`,
      (value, status, headers) => {
        const page = decodePage(
          value,
          status,
          headers,
          actor,
          decodeProduct,
          after,
        );
        if (
          category !== null &&
          page.items.some((item) => item.category_id !== category)
        )
          throw new Error("Unmatched category filter");
        return page;
      },
      options,
    ),
  categories: (actor: string, after?: string, options: RequestOptions = {}) =>
    getJson(
      `/catalog/categories?limit=25${after ? `&after=${id(after)}` : ""}`,
      (value, status, headers) =>
        decodePage(value, status, headers, actor, decodeCategory, after),
      options,
    ),
  detail: (actor: string, target: string, options: RequestOptions = {}) =>
    getJson(
      `/catalog/products/${id(target)}`,
      (value, status, headers) => {
        bound(headers, actor, status);
        const product = decodeProduct(value);
        if (product.id !== target) throw new Error("Unmatched product");
        return product;
      },
      options,
    ),
  createCategory: (
    actor: string,
    value: string,
    options: RequestOptions = {},
  ) =>
    create(
      "categories",
      actor,
      { name: name(value.trim()) },
      (value) => {
        const category = decodeCategory(value);
        if (!category.is_active) throw new Error("Inactive creation");
        return category;
      },
      options,
    ),
  createProduct: (
    actor: string,
    data: NewProduct,
    options: RequestOptions = {},
  ) =>
    create(
      "products",
      actor,
      {
        name: name(data.name.trim()),
        price: priceInput(data.price),
        sku: data.sku === null ? null : name(data.sku.trim(), 100),
        category_id: data.category_id === null ? null : id(data.category_id),
      },
      (value) => {
        const product = decodeProduct(value);
        if (!product.is_active) throw new Error("Inactive creation");
        return product;
      },
      options,
    ),
};
