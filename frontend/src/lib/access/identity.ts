import { getJson, sendJson, createJson } from "@/lib/api/client";
import {
  accessApi,
  readCsrfCookie,
  decodeLogin,
  type LoginResult,
} from "@/lib/access/api";

type Setup = { status: "confirm_factor"; seed: string; qr: string };
type Bundle = { codes: string[] };
export type SecurityState = {
  mfa_enabled: boolean;
  mailbox_verified: boolean;
  recent_seconds: number;
};
function empty(_value: unknown, status: number) {
  if (status !== 204) throw new Error("Invalid response");
}
function setup(value: unknown, status: number): Setup {
  const data = value as Partial<Setup>;
  if (
    status !== 200 ||
    data.status !== "confirm_factor" ||
    typeof data.seed !== "string" ||
    !/^[A-Z2-7]{32}$/.test(data.seed)
  )
    throw new Error("Invalid response");
  if (
    typeof data.qr !== "string" ||
    data.qr.length > 65536 ||
    !/^data:image\/svg\+xml;base64,[A-Za-z0-9+/]+=*$/.test(data.qr)
  )
    throw new Error("Invalid QR");
  return data as Setup;
}
function bundle(value: unknown, status: number): Bundle {
  const data = value as Partial<Bundle>;
  if (
    status !== 200 ||
    !Array.isArray(data.codes) ||
    data.codes.length !== 10 ||
    new Set(data.codes).size !== 10 ||
    data.codes.some((v) => typeof v !== "string" || !/^[A-Z2-7]{26}$/.test(v))
  )
    throw new Error("Invalid response");
  return data as Bundle;
}
async function post<T>(
  path: string,
  body: unknown,
  decode: (v: unknown, s: number) => T,
  actor?: string,
): Promise<T> {
  await accessApi.prepare();
  const csrf = readCsrfCookie(document.cookie, location.protocol === "https:");
  return actor
    ? createJson(path, body, csrf, actor, decode)
    : sendJson(path, body, csrf, decode);
}
export const identityApi = {
  totp: (code: string): Promise<LoginResult> =>
    post("/auth/mfa/totp", { code }, decodeLogin),
  requestEnrollment: () => post("/auth/mfa/enrollment/email", {}, empty),
  confirmEnrollment: (token: string) =>
    post("/auth/mfa/enrollment/confirm", { token }, setup),
  activate: (code: string, replacement: boolean) =>
    post(
      replacement
        ? "/auth/mfa/replacement/activate"
        : "/auth/mfa/enrollment/activate",
      { code },
      bundle,
    ),
  rotationBegin: (actor: string) =>
    post("/auth/mfa/rotation/begin", {}, setup, actor),
  rotationActivate: (actor: string, code: string) =>
    post("/auth/mfa/rotation/activate", { code }, bundle, actor),
  recover: (code: string) => post("/auth/mfa/recovery", { code }, setup),
  resetRequest: (email: string) =>
    post("/auth/password-reset/request", { email }, empty),
  reset: (token: string, password: string) =>
    post("/auth/password-reset/confirm", { token, password }, empty),
  verifyRequest: (actor: string) =>
    post("/auth/mailbox/request", {}, empty, actor),
  verify: (token: string, actor: string) =>
    post("/auth/mailbox/confirm", { token }, empty, actor),
  codes: (actor: string) => post("/auth/mfa/codes", {}, bundle, actor),
  revokeAll: (actor: string) =>
    post("/auth/sessions/revoke-all", {}, empty, actor),
  reauthenticate: (actor: string, password: string, code: string) =>
    post("/auth/mfa/reauthenticate", { password, code }, empty, actor),
  status: () =>
    getJson<SecurityState>("/auth/security", (value, status) => {
      const data = value as SecurityState;
      if (
        status !== 200 ||
        typeof data.mfa_enabled !== "boolean" ||
        typeof data.mailbox_verified !== "boolean" ||
        !Number.isInteger(data.recent_seconds) ||
        data.recent_seconds < 0 ||
        data.recent_seconds > 300
      )
        throw new Error("Invalid response");
      return data;
    }),
};

export type OwnSession = {
  session_id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
};
export type OwnEvent = { action: string; occurred_at: string };
export async function ownSessions(): Promise<OwnSession[]> {
  return getJson("/auth/sessions", (value, status) => {
    const data = value as { items: OwnSession[] };
    if (
      status !== 200 ||
      !Array.isArray(data.items) ||
      data.items.length > 100 ||
      data.items.some(
        (v) =>
          typeof v.session_id !== "string" ||
          typeof v.current !== "boolean" ||
          ![v.created_at, v.last_seen_at, v.expires_at].every(
            (t) => typeof t === "string" && Number.isFinite(Date.parse(t)),
          ),
      )
    )
      throw new Error("Invalid response");
    return data.items;
  });
}
export async function ownEvents(): Promise<OwnEvent[]> {
  return getJson("/auth/security/events", (value, status) => {
    const data = value as { items: OwnEvent[] };
    if (
      status !== 200 ||
      !Array.isArray(data.items) ||
      data.items.length > 50 ||
      data.items.some(
        (v) =>
          typeof v.action !== "string" ||
          !/^[a-z_]{1,32}$/.test(v.action) ||
          !Number.isFinite(Date.parse(v.occurred_at)),
      )
    )
      throw new Error("Invalid response");
    return data.items;
  });
}
