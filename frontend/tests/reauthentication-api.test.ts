import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { accessApi } from "@/lib/access/api";
import { getJson } from "@/lib/api/client";

const actor = "00000000-0000-4000-8000-000000000001";
const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
const locks = {
  request: vi.fn(
    async (
      _name: string,
      _options: { signal: AbortSignal },
      perform: () => Promise<Response>,
    ) => perform(),
  ),
};
beforeEach(() => {
  vi.stubGlobal("navigator", { locks });
  locks.request.mockClear();
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
});
afterEach(() => {
  vi.useRealTimers();
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});

describe("API de confirmación", () => {
  it("solo envía contraseña con CSRF y membresía esperada; valida 204 y contexto", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response(null, {
          status: 204,
          headers: { "X-Access-Membership": actor },
        }),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(
      accessApi.reauthenticate(actor, " sin recortar "),
    ).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[0][0]).toBe("/api/auth/csrf");
    expect(fetch.mock.calls[1][0]).toBe("/api/auth/reauthenticate");
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
      password: " sin recortar ",
    });
    expect(fetch.mock.calls[1][1].headers).toMatchObject({
      "X-CSRF-Token": proof,
      "X-Expected-Membership": actor,
    });
    expect(locks.request.mock.calls.map((call) => call[0])).toEqual([
      "carrillo_access_http",
      "carrillo_access_http",
    ]);
  });
  it.each([401, 403, 428, 429, 503])(
    "no reenvía ni expone cuerpo en %s",
    async (status) => {
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 204 }))
        .mockResolvedValueOnce(new Response("private-canary", { status }));
      vi.stubGlobal("fetch", fetch);
      await expect(
        accessApi.reauthenticate(actor, "synthetic"),
      ).rejects.toMatchObject({
        kind: "http",
        status,
        message: "No se pudo completar la solicitud.",
      });
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );
  it.each([200, 204])(
    "rechaza respuesta %s sin contexto esperado",
    async (status) => {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValueOnce(new Response(null, { status: 204 }))
          .mockResolvedValueOnce(
            status === 204
              ? new Response(null, { status })
              : Response.json({ status: "authenticated" }),
          ),
      );
      await expect(
        accessApi.reauthenticate(actor, "synthetic"),
      ).rejects.toMatchObject({ kind: "invalid-response" });
    },
  );
  it("sin Web Locks no prepara ni envía la contraseña", async () => {
    vi.stubGlobal("navigator", {});
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(
      accessApi.reauthenticate(actor, "synthetic"),
    ).rejects.toMatchObject({ kind: "csrf" });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("un GET también espera coordinación y puede cancelarse en cola sin fetch", async () => {
    vi.useFakeTimers();
    locks.request.mockImplementationOnce(
      async (_name, options) =>
        new Promise<Response>((_resolve, reject) => {
          options.signal.addEventListener(
            "abort",
            () => reject(new DOMException("private", "AbortError")),
            { once: true },
          );
        }),
    );
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const pending = expect(
      getJson("/auth/session", (value) => value),
    ).rejects.toMatchObject({ kind: "network" });
    expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(15_000);
    await pending;
    expect(fetch).not.toHaveBeenCalled();
    expect(locks.request).toHaveBeenCalledWith(
      "carrillo_access_http",
      { signal: expect.any(AbortSignal) },
      expect.any(Function),
    );
  });
});
