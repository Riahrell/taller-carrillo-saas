import { accessApi, readCsrfCookie } from "@/lib/access/api";
import { getJson, operationJson, type RequestOptions } from "@/lib/api/client";
import type { Page } from "@/lib/catalog/api";

export type Balance = {
  product_id: string;
  quantity: string;
  value: string;
  average_unit_cost: string | null;
};
export type CountInput = {
  product_id: string;
  counted_quantity: string;
  reason: string;
  declared_unit_cost: string | null;
};
export type Count = {
  id: string;
  product_id: string;
  operation_key: string;
  before_quantity: string;
  after_quantity: string;
  before_value: string;
  after_value: string;
  quantity_delta: string;
  value_delta: string;
  reason: string;
  declared_unit_cost: string | null;
  created_at: string;
};
export type PendingCount = { key: string; data: CountInput };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function id(value: unknown): string {
  if (typeof value !== "string" || value.length !== 36 || !UUID.test(value))
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
function decimal(
  value: unknown,
  scale: number,
  digits: number,
  signed = false,
): string {
  const pattern = new RegExp(
    `^${signed ? "-?" : ""}(0|[1-9][0-9]{0,${digits - 1}})\\.[0-9]{${scale}}$`,
  );
  if (typeof value !== "string" || pattern.exec(value)?.[0] !== value)
    throw new Error("Invalid decimal");
  return value;
}
function scaled(value: string): bigint {
  return BigInt(value.replace(".", ""));
}
function input(value: string, scale: number, digits: number): string {
  const pattern = new RegExp(
    `^(0|[1-9][0-9]{0,${digits - 1}})(?:\\.([0-9]{1,${scale}}))?$`,
  );
  const match = pattern.exec(value.trim().replace(",", "."));
  if (!match) throw new Error("Invalid decimal input");
  return `${match[1]}.${(match[2] ?? "").padEnd(scale, "0")}`;
}
export const quantityInput = (value: string) => input(value, 3, 15);
export const costInput = (value: string) => input(value, 6, 22);
export function reasonInput(value: string): string {
  const reason = value.trim();
  if (
    !reason ||
    Array.from(reason).length > 500 ||
    !reason.isWellFormed() ||
    reason.includes("\0")
  )
    throw new Error("Invalid reason");
  return reason;
}
export function displayDecimal(value: string, minimum = 0): string {
  const [whole, raw = ""] = value.split(".");
  let fraction = raw;
  while (fraction.length > minimum && fraction.endsWith("0"))
    fraction = fraction.slice(0, -1);
  return (
    whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") +
    (fraction ? `.${fraction}` : "")
  );
}
function bound(status: number, headers: Headers, actor: string) {
  if (status !== 200 || headers.get("X-Access-Membership") !== id(actor))
    throw new Error("Changed context");
}
export function decodeBalance(value: unknown, product: string): Balance {
  const data = object(value, [
    "product_id",
    "quantity",
    "value",
    "average_unit_cost",
  ]);
  if (id(data.product_id) !== id(product)) throw new Error("Changed product");
  const quantity = decimal(data.quantity, 3, 15);
  const total = decimal(data.value, 6, 22);
  const average =
    data.average_unit_cost === null
      ? null
      : decimal(data.average_unit_cost, 6, 25);
  const q = scaled(quantity),
    v = scaled(total);
  if (q === 0n) {
    if (v !== 0n || average !== null) throw new Error("Invalid empty stock");
  } else {
    const numerator = v * 1000n;
    const expected = numerator / q + (2n * (numerator % q) >= q ? 1n : 0n);
    if (average === null || scaled(average) !== expected)
      throw new Error("Invalid average");
  }
  return {
    product_id: product,
    quantity,
    value: total,
    average_unit_cost: average,
  };
}
function timeKey(value: string): string {
  const match =
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(?:Z|\+00:00)$/.exec(
      value,
    );
  if (
    !match ||
    !Number.isFinite(Date.parse(value)) ||
    new Date(value).toISOString().slice(0, 19) !== match[1]
  )
    throw new Error("Invalid time");
  return `${match[1]}.${(match[2] ?? "").padEnd(6, "0")}`;
}
export function decodeCount(value: unknown, product: string): Count {
  const data = object(value, [
    "id",
    "product_id",
    "operation_key",
    "before_quantity",
    "after_quantity",
    "before_value",
    "after_value",
    "quantity_delta",
    "value_delta",
    "reason",
    "declared_unit_cost",
    "created_at",
  ]);
  if (id(data.product_id) !== id(product)) throw new Error("Changed product");
  const before_quantity = decimal(data.before_quantity, 3, 15),
    after_quantity = decimal(data.after_quantity, 3, 15);
  const before_value = decimal(data.before_value, 6, 22),
    after_value = decimal(data.after_value, 6, 22);
  const quantity_delta = decimal(data.quantity_delta, 3, 15, true),
    value_delta = decimal(data.value_delta, 6, 22, true);
  const dq = scaled(quantity_delta),
    dv = scaled(value_delta);
  if (
    scaled(after_quantity) - scaled(before_quantity) !== dq ||
    scaled(after_value) - scaled(before_value) !== dv ||
    (scaled(before_quantity) === 0n && scaled(before_value) !== 0n) ||
    (scaled(after_quantity) === 0n && scaled(after_value) !== 0n) ||
    (dq === 0n && dv !== 0n) ||
    (dq > 0n && dv < 0n) ||
    (dq < 0n && dv > 0n)
  )
    throw new Error("Invalid count values");
  const declared_unit_cost =
    data.declared_unit_cost === null
      ? null
      : decimal(data.declared_unit_cost, 6, 22);
  const needsCost =
    dq > 0n && (scaled(before_quantity) === 0n || scaled(before_value) === 0n);
  if (needsCost !== (declared_unit_cost !== null))
    throw new Error("Invalid declared cost");
  if (typeof data.created_at !== "string") throw new Error("Invalid time");
  if (
    typeof data.reason !== "string" ||
    reasonInput(data.reason) !== data.reason
  )
    throw new Error("Invalid reason");
  timeKey(data.created_at);
  return {
    id: id(data.id),
    product_id: product,
    operation_key: id(data.operation_key),
    before_quantity,
    after_quantity,
    before_value,
    after_value,
    quantity_delta,
    value_delta,
    reason: data.reason,
    declared_unit_cost,
    created_at: data.created_at,
  };
}
function decodePage(
  value: unknown,
  product: string,
  after?: Count,
): Page<Count> {
  const data = object(value, ["items", "next_after"]);
  if (!Array.isArray(data.items) || data.items.length > 25)
    throw new Error("Invalid page");
  const items = data.items.map((row) => decodeCount(row, product));
  let previous = after ? `${timeKey(after.created_at)}|${after.id}` : null;
  for (const item of items) {
    const current = `${timeKey(item.created_at)}|${item.id}`;
    if (previous !== null && current >= previous)
      throw new Error("Invalid order");
    previous = current;
  }
  const next = data.next_after === null ? null : id(data.next_after);
  if (next !== null && (items.length !== 25 || next !== items.at(-1)?.id))
    throw new Error("Invalid cursor");
  return { items, next_after: next };
}
export const inventoryApi = {
  balance: (actor: string, product: string, options: RequestOptions = {}) =>
    getJson(
      `/inventory/products/${id(product)}/balance`,
      (value, status, headers) => {
        bound(status, headers, actor);
        return decodeBalance(value, product);
      },
      options,
    ),
  counts: (
    actor: string,
    product: string,
    after?: Count,
    options: RequestOptions = {},
  ) =>
    getJson(
      `/inventory/products/${id(product)}/counts?limit=25${after ? `&after=${id(after.id)}` : ""}`,
      (value, status, headers) => {
        bound(status, headers, actor);
        return decodePage(value, product, after);
      },
      options,
    ),
  record: async (
    actor: string,
    pending: PendingCount,
    options: RequestOptions = {},
  ): Promise<Count> => {
    const key = id(pending.key);
    const data = {
      product_id: id(pending.data.product_id),
      counted_quantity: quantityInput(pending.data.counted_quantity),
      reason: reasonInput(pending.data.reason),
      declared_unit_cost:
        pending.data.declared_unit_cost === null
          ? null
          : costInput(pending.data.declared_unit_cost),
    };
    id(actor);
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      window.location.protocol === "https:",
    );
    return operationJson(
      "/inventory/counts",
      data,
      csrf,
      actor,
      key,
      (value, status, headers) => {
        bound(status, headers, actor);
        const result = decodeCount(value, data.product_id);
        if (
          result.operation_key !== key ||
          result.after_quantity !== data.counted_quantity ||
          result.reason !== data.reason ||
          result.declared_unit_cost !== data.declared_unit_cost
        )
          throw new Error("Changed operation");
        return result;
      },
      options,
    );
  },
};
