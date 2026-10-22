import { accessApi, readCsrfCookie } from "@/lib/access/api";
import { operationJson, type RequestOptions } from "@/lib/api/client";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export async function withdraw(
  actor: string,
  productId: string,
  quantity: string,
  reason: string,
  operationKey: string,
  options: RequestOptions = {},
) {
  if (![actor, productId, operationKey].every((value) => UUID.test(value)))
    throw new Error("Invalid context");
  await accessApi.prepare(options);
  return operationJson(
    "/inventory/availability/withdrawals",
    { product_id: productId, quantity, reason },
    readCsrfCookie(document.cookie, location.protocol === "https:"),
    actor,
    operationKey,
    (value, status, headers) => {
      if (
        status !== 200 ||
        headers.get("X-Access-Membership") !== actor ||
        typeof value !== "object" ||
        value === null ||
        Array.isArray(value)
      )
        throw new Error("Invalid response");
      const row = value as Record<string, unknown>;
      const fields = [
        "id",
        "product_id",
        "operation_key",
        "before_quantity",
        "after_quantity",
        "quantity_delta",
        "reason",
        "created_at",
      ];
      if (
        Object.keys(row).sort().join(",") !== fields.sort().join(",") ||
        row.product_id !== productId ||
        row.operation_key !== operationKey ||
        row.reason !== reason ||
        typeof row.id !== "string" ||
        !UUID.test(row.id) ||
        typeof row.created_at !== "string" ||
        !Number.isFinite(Date.parse(row.created_at))
      )
        throw new Error("Invalid receipt");
      for (const key of [
        "before_quantity",
        "after_quantity",
        "quantity_delta",
      ]) {
        if (
          typeof row[key] !== "string" ||
          !(
            key === "quantity_delta"
              ? /^-(?:0|[1-9][0-9]{0,14})\.[0-9]{3}$/
              : /^(?:0|[1-9][0-9]{0,14})\.[0-9]{3}$/
          ).test(row[key])
        )
          throw new Error("Invalid quantity");
      }
      return { id: row.id, after_quantity: row.after_quantity as string };
    },
    options,
  );
}
