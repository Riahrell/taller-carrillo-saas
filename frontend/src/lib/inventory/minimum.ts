import { accessApi, readCsrfCookie } from "@/lib/access/api";
import { getJson, patchJson, type RequestOptions } from "@/lib/api/client";
import { quantityInput } from "@/lib/inventory/api";

export type StockMinimum = {
  product_id: string;
  minimum: string | null;
  quantity: string;
  is_active: boolean;
  is_low: boolean;
  etag: string;
};
function id(value: string) {
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      value,
    )
  )
    throw new Error("Invalid ID");
  return value;
}
function version(value: string | null, product: string) {
  if (
    value === null ||
    !new RegExp(
      `^"stock-minimum-v1-${id(product)}-(0|[1-9][0-9]{0,18})"$`,
    ).test(value)
  )
    throw new Error("Invalid version");
  return value;
}
function decode(
  value: unknown,
  status: number,
  headers: Headers,
  actor: string,
  target: string,
): StockMinimum {
  if (
    status !== 200 ||
    headers.get("X-Access-Membership") !== id(actor) ||
    typeof value !== "object" ||
    !value ||
    Array.isArray(value)
  )
    throw new Error("Changed context");
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).sort().join(",") !==
      "is_active,is_low,minimum,product_id,quantity" ||
    data.product_id !== target ||
    typeof data.quantity !== "string" ||
    quantityInput(data.quantity) !== data.quantity ||
    typeof data.is_active !== "boolean" ||
    typeof data.is_low !== "boolean"
  )
    throw new Error("Invalid minimum");
  const minimum = data.minimum;
  if (
    minimum !== null &&
    (typeof minimum !== "string" || quantityInput(minimum) !== minimum)
  )
    throw new Error("Invalid minimum");
  const low =
    data.is_active &&
    minimum !== null &&
    BigInt(data.quantity.replace(".", "")) <= BigInt(minimum.replace(".", ""));
  if (data.is_low !== low) throw new Error("Invalid notice");
  return {
    product_id: target,
    minimum: minimum as string | null,
    quantity: data.quantity,
    is_active: data.is_active,
    is_low: data.is_low,
    etag: version(headers.get("ETag"), target),
  };
}
export const stockMinimumApi = {
  read: (actor: string, target: string, options: RequestOptions = {}) =>
    getJson(
      `/inventory/products/${id(target)}/minimum`,
      (value, status, headers) => decode(value, status, headers, actor, target),
      options,
    ),
  save: async (
    actor: string,
    target: string,
    etag: string,
    minimum: string | null,
    options: RequestOptions = {},
  ) => {
    id(actor);
    id(target);
    version(etag, target);
    const canonical = minimum === null ? null : quantityInput(minimum);
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      window.location.protocol === "https:",
    );
    return patchJson(
      `/inventory/products/${target}/minimum`,
      { minimum: canonical },
      csrf,
      etag,
      actor,
      (value, status, headers) => {
        const result = decode(value, status, headers, actor, target);
        if (result.minimum !== canonical) throw new Error("Changed setting");
        return result;
      },
      options,
    );
  },
};
