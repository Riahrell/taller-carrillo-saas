import { getJson, type RequestOptions } from "@/lib/api/client";
import { quantityInput } from "@/lib/inventory/api";
import type { Page } from "@/lib/catalog/api";

export type LowStockItem = {
  id: string;
  name: string;
  sku: string | null;
  quantity: string;
  minimum: string;
};
function id(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      value,
    )
  )
    throw new Error("Invalid ID");
  return value;
}
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(",") !== [...keys].sort().join(",")
  )
    throw new Error("Invalid fields");
  return value as Record<string, unknown>;
}
function text(value: unknown, max: number): string {
  if (
    typeof value !== "string" ||
    value.trim() !== value ||
    !value ||
    Array.from(value).length > max ||
    value.includes("\0") ||
    !value.isWellFormed()
  )
    throw new Error("Invalid text");
  return value;
}
export const lowStockApi = {
  list: (
    actor: string,
    after?: string,
    options: RequestOptions = {},
  ): Promise<Page<LowStockItem>> =>
    getJson(
      `/inventory/low-stock?limit=25${after ? `&after=${id(after)}` : ""}`,
      (value, status, headers) => {
        if (status !== 200 || headers.get("X-Access-Membership") !== id(actor))
          throw new Error("Changed context");
        const page = record(value, ["items", "next_after"]);
        if (!Array.isArray(page.items) || page.items.length > 25)
          throw new Error("Invalid page");
        const items = page.items.map((raw) => {
          const row = record(raw, ["id", "name", "sku", "quantity", "minimum"]);
          if (
            typeof row.quantity !== "string" ||
            typeof row.minimum !== "string" ||
            quantityInput(row.quantity) !== row.quantity ||
            quantityInput(row.minimum) !== row.minimum ||
            BigInt(row.quantity.replace(".", "")) >
              BigInt(row.minimum.replace(".", ""))
          )
            throw new Error("Invalid stock notice");
          return {
            id: id(row.id),
            name: text(row.name, 200),
            sku: row.sku === null ? null : text(row.sku, 100),
            quantity: row.quantity,
            minimum: row.minimum,
          };
        });
        if (
          items.some(
            (row, index) =>
              row.id <= (index ? items[index - 1].id : (after ?? "")),
          )
        )
          throw new Error("Invalid order");
        const next = page.next_after === null ? null : id(page.next_after);
        if (next !== null && (items.length !== 25 || next !== items.at(-1)?.id))
          throw new Error("Invalid cursor");
        return { items, next_after: next };
      },
      options,
    ),
};
