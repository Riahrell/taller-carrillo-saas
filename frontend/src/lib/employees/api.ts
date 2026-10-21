import { accessApi, readCsrfCookie } from "@/lib/access/api";
import {
  createJson,
  getJson,
  sendJson,
  type RequestOptions,
} from "@/lib/api/client";

export type Invitation = {
  id: string;
  email: string;
  created_at: string;
  expires_at: string;
  status: "pending" | "expired" | "revoked";
};
export type Applicant = { id: string; email: string; verified_at: string };
export type Page<T> = { items: T[]; next_after: string | null };
export type RegistrationStatus =
  "awaiting_code" | "pending_approval" | "approved";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value))
    throw new Error("Invalid ID");
  return value;
}
function record(value: unknown, fields: string[]): Record<string, unknown> {
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).sort().join(",") !== [...fields].sort().join(",")
  )
    throw new Error("Invalid fields");
  return value as Record<string, unknown>;
}
function email(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length > 254 ||
    !value.includes("@") ||
    value.trim() !== value
  )
    throw new Error("Invalid email");
  return value;
}
function date(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length > 40 ||
    !Number.isFinite(Date.parse(value))
  )
    throw new Error("Invalid date");
  return value;
}
function invitation(value: unknown): Invitation {
  const row = record(value, [
    "id",
    "email",
    "created_at",
    "expires_at",
    "status",
  ]);
  if (!["pending", "expired", "revoked"].includes(String(row.status)))
    throw new Error("Invalid status");
  const created = date(row.created_at),
    expires = date(row.expires_at);
  if (Date.parse(expires) - Date.parse(created) !== 86400000)
    throw new Error("Invalid expiry");
  return {
    id: id(row.id),
    email: email(row.email),
    created_at: created,
    expires_at: expires,
    status: row.status as Invitation["status"],
  };
}
function applicant(value: unknown): Applicant {
  const row = record(value, ["id", "email", "verified_at"]);
  return {
    id: id(row.id),
    email: email(row.email),
    verified_at: date(row.verified_at),
  };
}
function bound(headers: Headers, actor: string) {
  if (headers.get("X-Access-Membership") !== id(actor))
    throw new Error("Context changed");
}
export function decodeEmployeePage<T extends { id: string }>(
  value: unknown,
  decode: (v: unknown) => T,
  after?: string,
): Page<T> {
  const row = record(value, ["items", "next_after"]);
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
function status(value: unknown, code: number): RegistrationStatus {
  const row = record(value, ["status"]);
  if (
    ![200, 202].includes(code) ||
    !["awaiting_code", "pending_approval", "approved"].includes(
      String(row.status),
    )
  )
    throw new Error("Invalid registration status");
  return row.status as RegistrationStatus;
}
async function post<T>(
  path: string,
  body: unknown,
  decode: (v: unknown, s: number, h: Headers) => T,
  actor?: string,
  options: RequestOptions = {},
) {
  await accessApi.prepare(options);
  const csrf = readCsrfCookie(document.cookie, location.protocol === "https:");
  return actor
    ? createJson(
        path,
        body,
        csrf,
        optionsActor(actor),
        decodeActor(actor, decode),
        options,
      )
    : sendJson(path, body, csrf, decode, options);
}
// Keep the actor in the dedicated header, never in the JSON body.
function optionsActor(actor: string) {
  return id(actor);
}
function decodeActor<T>(
  actor: string,
  decode: (v: unknown, s: number, h: Headers) => T,
) {
  return (v: unknown, s: number, h: Headers) => {
    bound(h, actor);
    return decode(v, s, h);
  };
}
export const employeesApi = {
  invitations: (actor: string, after?: string, options: RequestOptions = {}) =>
    getJson(
      `/auth/employee-invitations${after ? `?after=${id(after)}` : ""}`,
      (v, s, h) => {
        bound(h, actor);
        if (s !== 200) throw new Error("Invalid status");
        return decodeEmployeePage(v, invitation, after);
      },
      options,
    ),
  pending: (actor: string, after?: string, options: RequestOptions = {}) =>
    getJson(
      `/auth/employee-registrations${after ? `?after=${id(after)}` : ""}`,
      (v, s, h) => {
        bound(h, actor);
        if (s !== 200) throw new Error("Invalid status");
        return decodeEmployeePage(v, applicant, after);
      },
      options,
    ),
  invite: (actor: string, address: string, options: RequestOptions = {}) =>
    post(
      "/auth/employee-invitations",
      { email: address },
      (v, s) => {
        if (s !== 201) throw new Error("Invalid status");
        const row = record(v, ["invitation", "code"]);
        const item = invitation(row.invitation);
        if (
          typeof row.code !== "string" ||
          !/^[A-Za-z0-9_-]{43}$/.test(row.code) ||
          item.email !== address.trim().toLowerCase()
        )
          throw new Error("Invalid invitation");
        return { invitation: item, code: row.code };
      },
      actor,
      options,
    ),
  revoke: (actor: string, invitationId: string, options: RequestOptions = {}) =>
    post(
      `/auth/employee-invitations/${id(invitationId)}/revoke`,
      {},
      (v, s) => {
        const item = invitation(v);
        if (s !== 200 || item.id !== invitationId || item.status !== "revoked")
          throw new Error("Invalid revocation");
        return item;
      },
      actor,
      options,
    ),
  approve: (
    actor: string,
    registrationId: string,
    options: RequestOptions = {},
  ) =>
    post(
      `/auth/employee-registrations/${id(registrationId)}/approve`,
      {},
      (v, s) => {
        const row = record(v, ["membership_id", "role"]);
        if (s !== 200 || row.role !== "read_only")
          throw new Error("Invalid approval");
        return id(row.membership_id);
      },
      actor,
      options,
    ),
};
export const registrationApi = {
  prepare: (options: RequestOptions = {}) => accessApi.prepare(options),
  status: (options: RequestOptions = {}) =>
    getJson("/auth/employee-registration/status", status, options),
  start: (
    data: { email: string; password: string; invitation: string },
    options: RequestOptions = {},
  ) => post("/auth/employee-registration", data, status, undefined, options),
  confirm: (code: string, options: RequestOptions = {}) =>
    post(
      "/auth/employee-registration/confirm",
      { code },
      status,
      undefined,
      options,
    ),
  resend: (options: RequestOptions = {}) =>
    post(
      "/auth/employee-registration/resend",
      {},
      (_v, s) => {
        if (s !== 204) throw new Error("Invalid status");
      },
      undefined,
      options,
    ),
};
