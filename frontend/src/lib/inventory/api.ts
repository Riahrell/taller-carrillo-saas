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
export type ManualInput = {
  product_id: string;
  direction: "IN" | "OUT";
  quantity: string;
  reason: string;
  unit_cost: string | null;
};
export type ManualMovement = Count & { direction: "IN" | "OUT" };
export type HistoryEntry = {
  id: string;
  product_id: string;
  source: "COUNT" | "MANUAL" | "RECEIPT" | "REVERSAL";
  direction: "IN" | "OUT" | null;
  source_document_id: string;
  recorded_by: string;
  reason: string | null;
  before_quantity: string;
  after_quantity: string;
  before_value: string;
  after_value: string;
  quantity_delta: string;
  value_delta: string;
  created_at: string;
};
export type ReversalInput = {
  product_id: string;
  source: "COUNT" | "MANUAL";
  original_id: string;
  reason: string;
};
export type ReversalResult = Omit<Count, "declared_unit_cost"> & {
  original_source: "COUNT" | "MANUAL";
  original_id: string;
};
export type PendingCount = {
  key: string;
  data: CountInput | ManualInput | ReversalInput;
};

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
export function decodeCount(
  value: unknown,
  product: string,
  manualDirection?: "IN" | "OUT",
): Count {
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
  const needsCost = manualDirection
    ? manualDirection === "IN"
    : dq > 0n &&
      (scaled(before_quantity) === 0n || scaled(before_value) === 0n);
  if (
    (manualDirection === "IN" && dq <= 0n) ||
    (manualDirection === "OUT" && dq >= 0n)
  )
    throw new Error("Invalid direction");
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
export function decodeManual(value: unknown, product: string): ManualMovement {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid movement");
  const { direction, ...rest } = value as Record<string, unknown>;
  if (direction !== "IN" && direction !== "OUT")
    throw new Error("Invalid direction");
  return { ...decodeCount(rest, product, direction), direction };
}
function decodePage(
  value: unknown,
  product: string,
  after?: Count,
  manual = false,
): Page<Count> {
  const data = object(value, ["items", "next_after"]);
  if (!Array.isArray(data.items) || data.items.length > 25)
    throw new Error("Invalid page");
  const items = data.items.map((row) =>
    manual ? decodeManual(row, product) : decodeCount(row, product),
  );
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
function historyCursor(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid cursor");
  const [source, key, extra] = value.split(":");
  if (
    !["COUNT", "MANUAL", "RECEIPT", "REVERSAL"].includes(source) ||
    extra !== undefined
  )
    throw new Error("Invalid cursor");
  return `${source}:${id(key)}`;
}
export function decodeHistory(value: unknown, product: string): HistoryEntry {
  const row = object(value, [
    "id",
    "product_id",
    "source",
    "direction",
    "source_document_id",
    "recorded_by",
    "reason",
    "before_quantity",
    "after_quantity",
    "before_value",
    "after_value",
    "quantity_delta",
    "value_delta",
    "created_at",
  ]);
  const source = row.source;
  if (
    source !== "COUNT" &&
    source !== "MANUAL" &&
    source !== "RECEIPT" &&
    source !== "REVERSAL"
  )
    throw new Error("Invalid source");
  if (id(row.product_id) !== id(product)) throw new Error("Changed product");
  const key = id(row.id),
    documentId = id(row.source_document_id);
  if (source !== "RECEIPT" && source !== "REVERSAL" && key !== documentId)
    throw new Error("Changed source");
  const direction = row.direction;
  if (
    source === "MANUAL"
      ? direction !== "IN" && direction !== "OUT"
      : direction !== null
  )
    throw new Error("Invalid direction");
  if (
    typeof row.recorded_by !== "string" ||
    !row.recorded_by.trim() ||
    row.recorded_by.length > 254 ||
    row.recorded_by.includes("\0")
  )
    throw new Error("Invalid actor");
  const reason = row.reason;
  if (
    source === "RECEIPT"
      ? reason !== null
      : typeof reason !== "string" || reasonInput(reason) !== reason
  )
    throw new Error("Invalid reason");
  const before_quantity = decimal(row.before_quantity, 3, 15),
    after_quantity = decimal(row.after_quantity, 3, 15);
  const before_value = decimal(row.before_value, 6, 22),
    after_value = decimal(row.after_value, 6, 22);
  const quantity_delta = decimal(row.quantity_delta, 3, 15, true),
    value_delta = decimal(row.value_delta, 6, 22, true);
  const dq = scaled(quantity_delta),
    dv = scaled(value_delta);
  if (
    scaled(after_quantity) - scaled(before_quantity) !== dq ||
    scaled(after_value) - scaled(before_value) !== dv ||
    (scaled(before_quantity) === 0n && scaled(before_value) !== 0n) ||
    (scaled(after_quantity) === 0n && scaled(after_value) !== 0n) ||
    (dq === 0n && (dv !== 0n || source === "REVERSAL")) ||
    (dq > 0n && dv < 0n) ||
    (dq < 0n && dv > 0n) ||
    ((source === "RECEIPT" || direction === "IN") && dq <= 0n) ||
    (direction === "OUT" && dq >= 0n)
  )
    throw new Error("Invalid stock evidence");
  if (typeof row.created_at !== "string") throw new Error("Invalid time");
  timeKey(row.created_at);
  return {
    id: key,
    product_id: product,
    source,
    direction: direction as HistoryEntry["direction"],
    source_document_id: documentId,
    recorded_by: row.recorded_by,
    reason: reason as string | null,
    before_quantity,
    after_quantity,
    before_value,
    after_value,
    quantity_delta,
    value_delta,
    created_at: row.created_at,
  };
}
function decodeHistoryPage(
  value: unknown,
  product: string,
  after?: HistoryEntry,
): Page<HistoryEntry> {
  const page = object(value, ["items", "next_after"]);
  if (!Array.isArray(page.items) || page.items.length > 25)
    throw new Error("Invalid page");
  const items = page.items.map((row) => decodeHistory(row, product));
  const order = (row: HistoryEntry) =>
    `${timeKey(row.created_at)}|${row.source}|${row.id}`;
  let previous = after ? order(after) : null;
  for (const row of items) {
    const current = order(row);
    if (previous !== null && current >= previous)
      throw new Error("Invalid order");
    previous = current;
  }
  const next = page.next_after === null ? null : historyCursor(page.next_after);
  const last = items.at(-1);
  if (
    next !== null &&
    (items.length !== 25 || !last || next !== `${last.source}:${last.id}`)
  )
    throw new Error("Invalid cursor");
  return { items, next_after: next };
}
export const inventoryApi = {
  history: (
    actor: string,
    product: string,
    after?: HistoryEntry,
    options: RequestOptions = {},
  ) =>
    getJson(
      `/inventory/products/${id(product)}/history?limit=25${after ? `&after=${encodeURIComponent(historyCursor(`${after.source}:${after.id}`))}` : ""}`,
      (value, status, headers) => {
        bound(status, headers, actor);
        return decodeHistoryPage(value, product, after);
      },
      options,
    ),
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
  manuals: (
    actor: string,
    product: string,
    after?: Count,
    options: RequestOptions = {},
  ) =>
    getJson(
      `/inventory/products/${id(product)}/manual-movements?limit=25${after ? `&after=${id(after.id)}` : ""}`,
      (value, status, headers) => {
        bound(status, headers, actor);
        return decodePage(value, product, after, true) as Page<ManualMovement>;
      },
      options,
    ),
  record: async (
    actor: string,
    pending: PendingCount,
    options: RequestOptions = {},
  ): Promise<Count | ReversalResult> => {
    if ("source" in pending.data)
      return recordReversal(actor, pending.key, pending.data, options);
    const key = id(pending.key);
    const raw = pending.data;
    const data: CountInput | ManualInput =
      "direction" in raw
        ? {
            product_id: id(raw.product_id),
            direction: raw.direction,
            quantity: quantityInput(raw.quantity),
            reason: reasonInput(raw.reason),
            unit_cost: raw.unit_cost === null ? null : costInput(raw.unit_cost),
          }
        : {
            product_id: id(raw.product_id),
            counted_quantity: quantityInput(raw.counted_quantity),
            reason: reasonInput(raw.reason),
            declared_unit_cost:
              raw.declared_unit_cost === null
                ? null
                : costInput(raw.declared_unit_cost),
          };
    if (
      "direction" in data &&
      (scaled(data.quantity) <= 0n ||
        (data.direction === "IN") !== (data.unit_cost !== null))
    )
      throw new Error("Invalid movement input");
    id(actor);
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      window.location.protocol === "https:",
    );
    return operationJson(
      "direction" in data ? "/inventory/movements" : "/inventory/counts",
      data,
      csrf,
      actor,
      key,
      (value, status, headers) => {
        bound(status, headers, actor);
        const result =
          "direction" in data
            ? decodeManual(value, data.product_id)
            : decodeCount(value, data.product_id);
        if (result.operation_key !== key || result.reason !== data.reason)
          throw new Error("Changed operation");
        if ("direction" in data) {
          const movement = result as ManualMovement;
          const expected =
            scaled(data.quantity) * (data.direction === "IN" ? 1n : -1n);
          if (
            movement.direction !== data.direction ||
            scaled(movement.quantity_delta) !== expected ||
            movement.declared_unit_cost !== data.unit_cost
          )
            throw new Error("Changed operation");
        } else if (
          result.after_quantity !== data.counted_quantity ||
          result.declared_unit_cost !== data.declared_unit_cost
        )
          throw new Error("Changed operation");
        return result;
      },
      options,
    );
  },
};

function decodeReversal(value: unknown, product: string): ReversalResult {
  const row = object(value, [
    "id",
    "product_id",
    "operation_key",
    "original_source",
    "original_id",
    "reason",
    "before_quantity",
    "after_quantity",
    "before_value",
    "after_value",
    "quantity_delta",
    "value_delta",
    "created_at",
  ]);
  if (
    id(row.product_id) !== id(product) ||
    (row.original_source !== "COUNT" && row.original_source !== "MANUAL")
  )
    throw new Error("Changed source");
  const before_quantity = decimal(row.before_quantity, 3, 15),
    after_quantity = decimal(row.after_quantity, 3, 15);
  const before_value = decimal(row.before_value, 6, 22),
    after_value = decimal(row.after_value, 6, 22);
  const quantity_delta = decimal(row.quantity_delta, 3, 15, true),
    value_delta = decimal(row.value_delta, 6, 22, true);
  const dq = scaled(quantity_delta),
    dv = scaled(value_delta);
  if (
    dq === 0n ||
    scaled(after_quantity) - scaled(before_quantity) !== dq ||
    scaled(after_value) - scaled(before_value) !== dv ||
    (dq > 0n && dv < 0n) ||
    (dq < 0n && dv > 0n) ||
    (scaled(before_quantity) === 0n && scaled(before_value) !== 0n) ||
    (scaled(after_quantity) === 0n && scaled(after_value) !== 0n)
  )
    throw new Error("Invalid compensation");
  if (
    typeof row.reason !== "string" ||
    reasonInput(row.reason) !== row.reason ||
    typeof row.created_at !== "string"
  )
    throw new Error("Invalid evidence");
  timeKey(row.created_at);
  return {
    id: id(row.id),
    product_id: product,
    operation_key: id(row.operation_key),
    original_source: row.original_source,
    original_id: id(row.original_id),
    reason: row.reason,
    before_quantity,
    after_quantity,
    before_value,
    after_value,
    quantity_delta,
    value_delta,
    created_at: row.created_at,
  };
}
async function recordReversal(
  actor: string,
  operationKey: string,
  raw: ReversalInput,
  options: RequestOptions,
): Promise<ReversalResult> {
  id(actor);
  const key = id(operationKey);
  if (raw.source !== "COUNT" && raw.source !== "MANUAL")
    throw new Error("Invalid source");
  const data = {
    product_id: id(raw.product_id),
    source: raw.source,
    original_id: id(raw.original_id),
    reason: reasonInput(raw.reason),
  };
  await accessApi.prepare(options);
  const csrf = readCsrfCookie(
    document.cookie,
    window.location.protocol === "https:",
  );
  return operationJson(
    "/inventory/reversals",
    data,
    csrf,
    actor,
    key,
    (value, status, headers) => {
      bound(status, headers, actor);
      const result = decodeReversal(value, data.product_id);
      if (
        result.operation_key !== key ||
        result.original_source !== data.source ||
        result.original_id !== data.original_id ||
        result.reason !== data.reason
      )
        throw new Error("Changed operation");
      return result;
    },
    options,
  );
}
