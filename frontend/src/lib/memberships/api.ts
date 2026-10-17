import { accessApi, readCsrfCookie, type Role } from "@/lib/access/api";
import {
  createJson,
  getJson,
  patchJson,
  type RequestOptions,
} from "@/lib/api/client";

export type Membership = {
  membership_id: string;
  role: Role;
  is_active: boolean;
};
export type Member = Membership & { email: string; user_is_active: boolean };
export type Page = { items: Member[]; next_after: string | null };
export type Detail = Membership & { etag: string };
export type NewUser = { email: string; password: string; role: Role };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const ETAG = /^"membership-v1-[0-9a-f]{64}"$/;
export const roleNames: Record<Role, string> = {
  admin: "Administrador",
  cashier: "Cajero",
  mechanic: "Mecánico",
  read_only: "Solo lectura",
};
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value))
    throw new Error("Invalid ID");
  return value;
}
function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("Invalid data");
  return value as Record<string, unknown>;
}
function membership(value: unknown): Membership {
  const data = object(value);
  if (
    typeof data.role !== "string" ||
    !Object.hasOwn(roleNames, data.role) ||
    typeof data.is_active !== "boolean"
  )
    throw new Error("Invalid membership");
  return {
    membership_id: id(data.membership_id),
    role: data.role as Role,
    is_active: data.is_active,
  };
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
export function decodePage(
  value: unknown,
  status: number,
  headers: Headers,
  actor: string,
  after?: string,
): Page {
  bound(headers, actor, status);
  const data = object(value);
  if (!Array.isArray(data.items) || data.items.length > 25)
    throw new Error("Invalid page");
  const items = data.items.map((value): Member => {
    const data = object(value);
    if (
      typeof data.email !== "string" ||
      !data.email.trim() ||
      Array.from(data.email).length > 254 ||
      typeof data.user_is_active !== "boolean"
    )
      throw new Error("Invalid member");
    return {
      ...membership(data),
      email: data.email,
      user_is_active: data.user_is_active,
    };
  });
  if (
    items.some(
      (item, index) =>
        item.membership_id <=
        (index ? items[index - 1].membership_id : (after ?? "")),
    )
  )
    throw new Error("Invalid order");
  const next = data.next_after === null ? null : id(data.next_after);
  if (
    next !== null &&
    (items.length !== 25 || next !== items.at(-1)?.membership_id)
  )
    throw new Error("Invalid cursor");
  return { items, next_after: next };
}
export function decodeDetail(
  value: unknown,
  status: number,
  headers: Headers,
  actor: string,
  target: string,
): Detail {
  bound(headers, actor, status);
  const current = membership(value),
    etag = headers.get("ETag");
  if (current.membership_id !== id(target) || !etag || !ETAG.test(etag))
    throw new Error("Invalid detail");
  return { ...current, etag };
}
export const membershipsApi = {
  create: async (
    actor: string,
    data: NewUser,
    options: RequestOptions = {},
  ) => {
    id(actor);
    if (
      !Object.hasOwn(roleNames, data.role) ||
      !data.email.trim() ||
      Array.from(data.email).length > 254 ||
      Array.from(data.password).length < 15 ||
      Array.from(data.password).length > 128
    )
      throw new TypeError("Invalid creation");
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      window.location.protocol === "https:",
    );
    return createJson(
      "/auth/memberships",
      { email: data.email, password: data.password, role: data.role },
      csrf,
      actor,
      (value, status, headers) => {
        bound(headers, actor, status, 201);
        const current = membership(value);
        const etag = headers.get("ETag");
        if (
          !etag ||
          !ETAG.test(etag) ||
          current.membership_id === actor ||
          current.role !== data.role ||
          !current.is_active ||
          headers.get("Location") !== `./memberships/${current.membership_id}`
        )
          throw new Error("Invalid creation result");
        return { ...current, etag };
      },
      options,
    );
  },
  page: (actor: string, after?: string, options: RequestOptions = {}) => {
    id(actor);
    return getJson(
      `/auth/memberships?limit=25${after ? `&after=${id(after)}` : ""}`,
      (value, status, headers) =>
        decodePage(value, status, headers, actor, after),
      options,
    );
  },
  detail: (actor: string, target: string, options: RequestOptions = {}) =>
    getJson(
      `/auth/memberships/${id(target)}`,
      (value, status, headers) =>
        decodeDetail(value, status, headers, actor, target),
      options,
    ),
  change: async (
    actor: string,
    target: string,
    desired: Pick<Membership, "role" | "is_active">,
    etag: string,
    options: RequestOptions = {},
  ) => {
    id(actor);
    id(target);
    if (
      !ETAG.test(etag) ||
      !Object.hasOwn(roleNames, desired.role) ||
      typeof desired.is_active !== "boolean"
    )
      throw new TypeError("Invalid change");
    await accessApi.prepare(options);
    const csrf = readCsrfCookie(
      document.cookie,
      window.location.protocol === "https:",
    );
    return patchJson(
      `/auth/memberships/${target}`,
      { role: desired.role, is_active: desired.is_active },
      csrf,
      etag,
      actor,
      (value, status, headers) => {
        const result = decodeDetail(value, status, headers, actor, target);
        if (
          result.role !== desired.role ||
          result.is_active !== desired.is_active
        )
          throw new Error("Unexpected result");
        return result;
      },
      options,
    );
  },
};
