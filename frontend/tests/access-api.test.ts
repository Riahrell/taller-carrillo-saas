import { afterEach, describe, expect, it, vi } from "vitest";

import {
  accessApi,
  decodeLogin,
  decodeSession,
  readCsrfCookie,
} from "@/lib/access/api";

const id = "00000000-0000-4000-8000-000000000001";
const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
afterEach(() => {
  vi.useRealTimers();
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});

describe("contrato de acceso", () => {
  it("acota espera de escritura y nunca la reenvía tras timeout", async () => {
    vi.useFakeTimers();
    document.cookie = `carrillo_csrf=${proof}; Path=/`;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockImplementationOnce(
        (_path, options: RequestInit) =>
          new Promise((_resolve, reject) => {
            options.signal?.addEventListener(
              "abort",
              () => reject(new DOMException("private-canary", "AbortError")),
              { once: true },
            );
          }),
      );
    vi.stubGlobal("fetch", fetch);
    const result = expect(
      accessApi.login("persona@example.com", "efímera"),
    ).rejects.toMatchObject({
      kind: "network",
      message: "No se pudo conectar con el servidor.",
    });
    await vi.advanceTimersByTimeAsync(15_000);
    await result;
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("renueva CSRF antes de enviar solo credenciales, sin controlar Origin ni cookies", async () => {
    document.cookie = `carrillo_csrf=${proof}; Path=/`;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ status: "authenticated" }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      accessApi.login("persona@example.com", " sin recortar "),
    ).resolves.toEqual({ status: "authenticated" });
    expect(fetch.mock.calls[0][0]).toBe("/api/auth/csrf");
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });
    const [path, options] = fetch.mock.calls[1];
    expect(path).toBe("/api/auth/login");
    expect(JSON.parse(options.body)).toEqual({
      email: "persona@example.com",
      password: " sin recortar ",
    });
    expect(options).toMatchObject({
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { "X-CSRF-Token": proof, "Content-Type": "application/json" },
    });
    expect(options.headers).not.toHaveProperty("Origin");
    expect(options.headers).not.toHaveProperty("Cookie");
  });

  it("no envía credenciales si no puede obtener prueba CSRF", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      accessApi.login("persona@example.com", "efímera"),
    ).rejects.toMatchObject({ kind: "csrf" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("selección solo propone membresía y logout envía un objeto vacío", async () => {
    document.cookie = `carrillo_csrf=${proof}; Path=/`;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ status: "authenticated" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await accessApi.select(id);
    await accessApi.logout();
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
      membership_id: id,
    });
    expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual({});
    expect(fetch.mock.calls[3][0]).toBe("/api/auth/logout");
  });

  it.each([401, 403, 429, 503])(
    "no reintenta escrituras ni expone el cuerpo al recibir %s",
    async (status) => {
      document.cookie = `carrillo_csrf=${proof}; Path=/`;
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 204 }))
        .mockResolvedValueOnce(new Response("sensitive-canary", { status }));
      vi.stubGlobal("fetch", fetch);
      await expect(
        accessApi.login("persona@example.com", "efímera"),
      ).rejects.toMatchObject({
        status,
        message: "No se pudo completar la solicitud.",
      });
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );

  it("usa solo la cookie CSRF del perfil seguro y rechaza ambigüedad", () => {
    expect(
      readCsrfCookie(`carrillo_csrf=bad; __Host-carrillo_csrf=${proof}`, true),
    ).toBe(proof);
    expect(() => readCsrfCookie(`carrillo_csrf=${proof}`, true)).toThrow();
    expect(() =>
      readCsrfCookie(`carrillo_csrf=${proof}; carrillo_csrf=${proof}`, false),
    ).toThrow();
    expect(() => readCsrfCookie("carrillo_session=private", false)).toThrow();
  });

  it.each([
    null,
    { role: "owner" },
    {
      user_id: id,
      membership_id: id,
      tenant_id: id,
      tenant_name: "Empresa",
      role: ["admin"],
    },
    {
      user_id: "not-uuid",
      membership_id: id,
      tenant_id: id,
      tenant_name: "Empresa",
      role: "admin",
    },
  ])("rechaza contexto de sesión inválido", (value) => {
    expect(() => decodeSession(value, 200)).toThrow();
  });

  it("decodifica contexto actual y no conserva propiedades adicionales", () => {
    const value = {
      user_id: id,
      membership_id: id,
      tenant_id: id,
      tenant_name: "Empresa",
      role: "read_only",
      arbitrary: "not-authority",
    };
    const decoded = decodeSession(value, 200);
    expect(decoded.role).toBe("read_only");
    expect(decoded).not.toHaveProperty("arbitrary");
  });

  it.each([
    { status: "authenticated" },
    { status: "select_membership", choices: [] },
    {
      status: "select_membership",
      choices: [
        { membership_id: id, tenant_name: "A" },
        { membership_id: id, tenant_name: "B" },
      ],
    },
  ])("rechaza selección inválida o semántica HTTP incorrecta", (value) => {
    expect(() => decodeLogin(value, 202)).toThrow();
  });
});
