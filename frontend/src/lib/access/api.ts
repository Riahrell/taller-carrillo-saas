import {
  ApiError,
  getJson,
  sendJson,
  type RequestOptions,
} from "@/lib/api/client";

export type Role = "admin" | "cashier" | "mechanic" | "read_only";
export type SessionContext = {
  user_id: string;
  membership_id: string;
  tenant_id: string;
  tenant_name: string;
  role: Role;
};
export type Choice = { membership_id: string; tenant_name: string };
export type LoginResult =
  | { status: "authenticated" }
  | { status: "select_membership"; choices: Choice[] };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PROOF = /^v1\.[1-9][0-9]{0,10}\.[A-Za-z0-9_-]{43}\.[0-9a-f]{64}$/;

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error("Invalid response");
  return value as Record<string, unknown>;
}
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value))
    throw new Error("Invalid ID");
  return value;
}
function name(value: unknown): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    Array.from(value).length > 200
  )
    throw new Error("Invalid name");
  return value;
}
export function decodeSession(value: unknown, status: number): SessionContext {
  const data = object(value);
  if (
    status !== 200 ||
    typeof data.role !== "string" ||
    !["admin", "cashier", "mechanic", "read_only"].includes(String(data.role))
  )
    throw new Error("Invalid session");
  return {
    user_id: id(data.user_id),
    membership_id: id(data.membership_id),
    tenant_id: id(data.tenant_id),
    tenant_name: name(data.tenant_name),
    role: data.role as Role,
  };
}
export function decodeLogin(value: unknown, status: number): LoginResult {
  const data = object(value);
  if (status === 200 && data.status === "authenticated")
    return { status: "authenticated" };
  if (
    status !== 202 ||
    data.status !== "select_membership" ||
    !Array.isArray(data.choices) ||
    data.choices.length < 2
  )
    throw new Error("Invalid login response");
  const choices = data.choices.map((value): Choice => {
    const choice = object(value);
    return {
      membership_id: id(choice.membership_id),
      tenant_name: name(choice.tenant_name),
    };
  });
  if (
    new Set(choices.map((choice) => choice.membership_id)).size !==
    choices.length
  )
    throw new Error("Duplicate choice");
  return { status: "select_membership", choices };
}
function empty(_value: unknown, status: number): void {
  if (status !== 204) throw new Error("Invalid empty response");
}
export function readCsrfCookie(raw: string, secure: boolean): string {
  const expected = (secure ? "__Host-" : "") + "carrillo_csrf";
  const values = raw
    .split(";")
    .map((part) => part.trim().split("="))
    .filter(([key]) => key === expected);
  if (
    values.length !== 1 ||
    values[0].length !== 2 ||
    !PROOF.test(values[0][1])
  ) {
    throw new ApiError(
      "csrf",
      "No pudimos preparar el acceso. Comprueba que las cookies estén habilitadas.",
    );
  }
  return values[0][1];
}
async function mutate<T>(
  path: string,
  body: unknown,
  decode: (value: unknown, status: number) => T,
  options: RequestOptions,
): Promise<T> {
  await getJson("/auth/csrf", empty, options);
  const csrf = readCsrfCookie(
    document.cookie,
    window.location.protocol === "https:",
  );
  return sendJson(path, body, csrf, decode, options);
}
export const accessApi = {
  session: (options: RequestOptions = {}) =>
    getJson("/auth/session", decodeSession, options),
  prepare: (options: RequestOptions = {}) =>
    getJson("/auth/csrf", empty, options),
  login: (email: string, password: string, options: RequestOptions = {}) =>
    mutate("/auth/login", { email, password }, decodeLogin, options),
  select: (membershipId: string, options: RequestOptions = {}) =>
    mutate(
      "/auth/select-membership",
      { membership_id: membershipId },
      (value, status) => {
        const result = decodeLogin(value, status);
        if (result.status !== "authenticated")
          throw new Error("Invalid selection response");
        return result;
      },
      options,
    ),
  logout: (options: RequestOptions = {}) =>
    mutate("/auth/logout", {}, empty, options),
};
