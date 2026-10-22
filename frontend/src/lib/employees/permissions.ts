import { accessApi, readCsrfCookie } from "@/lib/access/api";
import { getJson, patchJson, type RequestOptions } from "@/lib/api/client";
export type ReadPermission = {
  membership_id: string;
  granted: boolean;
  eligible: boolean;
  effective: boolean;
  revision: string;
  etag: string;
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function id(value: string) {
  if (!UUID.test(value)) throw new Error("Invalid ID");
  return value;
}
function decode(
  value: unknown,
  status: number,
  headers: Headers,
  actor: string,
  target: string,
  capability = "inventory-read",
): ReadPermission {
  if (
    status !== 200 ||
    headers.get("X-Access-Membership") !== id(actor) ||
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value)
  )
    throw new Error("Context changed");
  const row = value as Record<string, unknown>;
  if (
    Object.keys(row).sort().join(",") !==
      ["membership_id", "granted", "eligible", "effective", "revision"]
        .sort()
        .join(",") ||
    row.membership_id !== id(target) ||
    typeof row.granted !== "boolean" ||
    typeof row.eligible !== "boolean" ||
    typeof row.effective !== "boolean" ||
    row.effective !== (row.granted && row.eligible) ||
    typeof row.revision !== "string" ||
    !/^(0|[1-9][0-9]{0,18})$/.test(row.revision)
  )
    throw new Error("Invalid permission");
  const etag = `"${capability}-v1-${target}-${row.revision}"`;
  if (headers.get("ETag") !== etag) throw new Error("Invalid revision");
  return {
    membership_id: target,
    granted: row.granted,
    eligible: row.eligible,
    effective: row.effective,
    revision: row.revision,
    etag,
  };
}
export const permissionsApi = {
  current: (actor: string, target: string, options: RequestOptions = {}) =>
    getJson(
      `/auth/memberships/${id(target)}/inventory-read`,
      (v, s, h) => decode(v, s, h, actor, target),
      options,
      id(actor),
    ),
  async change(
    actor: string,
    target: string,
    granted: boolean,
    etag: string,
    options: RequestOptions = {},
  ) {
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      location.protocol === "https:",
    );
    return patchJson(
      `/auth/memberships/${id(target)}/inventory-read`,
      { granted },
      csrf,
      etag,
      id(actor),
      (v, s, h) => decode(v, s, h, actor, target),
      options,
    );
  },
};

export const withdrawalPermissionsApi = {
  current: (actor: string, target: string, options: RequestOptions = {}) =>
    getJson(
      `/auth/memberships/${id(target)}/inventory-withdrawal`,
      (v, s, h) => decode(v, s, h, actor, target, "inventory-withdrawal"),
      options,
      id(actor),
    ),
  async change(
    actor: string,
    target: string,
    granted: boolean,
    etag: string,
    options: RequestOptions = {},
  ) {
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      location.protocol === "https:",
    );
    return patchJson(
      `/auth/memberships/${id(target)}/inventory-withdrawal`,
      { granted },
      csrf,
      etag,
      id(actor),
      (v, s, h) => decode(v, s, h, actor, target, "inventory-withdrawal"),
      options,
    );
  },
};
