import { afterEach, describe, expect, it, vi } from "vitest";
import {
  membershipsApi,
  decodePage,
  decodeDetail,
} from "@/lib/memberships/api";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const etag = `"membership-v1-${"a".repeat(64)}"`;
const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
const member = { membership_id: target, role: "mechanic", is_active: true };
const row = { ...member, email: "prueba@example.com", user_is_active: true };
const headers = () => new Headers({ "X-Access-Membership": actor, ETag: etag });
afterEach(() => {
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
  vi.useRealTimers();
});

describe("API de membresías", () => {
  it("consulta página con límite fijo y contexto de respuesta comprobado", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: [row], next_after: null },
          { headers: headers() },
        ),
      );
    vi.stubGlobal("fetch", fetch);
    await expect(membershipsApi.page(actor)).resolves.toEqual({
      items: [row],
      next_after: null,
    });
    expect(fetch.mock.calls[0][0]).toBe("/api/auth/memberships?limit=25");
    expect(fetch.mock.calls[0][1]).toMatchObject({
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
    });
  });
  it("renueva CSRF y envía PATCH con If-Match, solo rol y estado", async () => {
    document.cookie = `carrillo_csrf=${proof}; Path=/`;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json(member, { headers: headers() }));
    vi.stubGlobal("fetch", fetch);
    await membershipsApi.change(
      actor,
      target,
      { role: "mechanic", is_active: true },
      etag,
    );
    expect(fetch.mock.calls[0][0]).toBe("/api/auth/csrf");
    const [path, init] = fetch.mock.calls[1];
    expect(path).toBe(`/api/auth/memberships/${target}`);
    expect(init).toMatchObject({
      method: "PATCH",
      headers: { "If-Match": etag, "X-CSRF-Token": proof },
    });
    expect(JSON.parse(init.body)).toEqual({
      role: "mechanic",
      is_active: true,
    });
    expect(init.headers).not.toHaveProperty("Origin");
    expect(init.headers).not.toHaveProperty("Cookie");
  });
  it.each([401, 403, 409, 412, 429, 503])(
    "no repite PATCH tras %s ni muestra detalles internos",
    async (status) => {
      document.cookie = `carrillo_csrf=${proof}; Path=/`;
      const fetch = vi
        .fn()
        .mockResolvedValueOnce(new Response(null, { status: 204 }))
        .mockResolvedValueOnce(new Response("private-canary", { status }));
      vi.stubGlobal("fetch", fetch);
      await expect(
        membershipsApi.change(
          actor,
          target,
          { role: "mechanic", is_active: true },
          etag,
        ),
      ).rejects.toMatchObject({
        status,
        message: "No se pudo completar la solicitud.",
      });
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );
  it.each([null, target])(
    "descarta respuesta de un contexto distinto o ausente: %s",
    (context) => {
      const meta = headers();
      if (context) meta.set("X-Access-Membership", context);
      else meta.delete("X-Access-Membership");
      expect(() =>
        decodePage({ items: [row], next_after: null }, 200, meta, actor),
      ).toThrow();
      expect(() => decodeDetail(member, 200, meta, actor, target)).toThrow();
    },
  );
  it.each([
    { items: [row, row], next_after: null },
    { items: [{ ...row, role: "root" }], next_after: null },
    { items: [{ ...row, is_active: "true" }], next_after: null },
    { items: [{ ...row, user_is_active: 1 }], next_after: null },
    { items: [], next_after: target },
    { items: [row], next_after: target },
  ])("rechaza página inválida", (page) => {
    expect(() => decodePage(page, 200, headers(), actor)).toThrow();
  });
  it("rechaza orden/cursor repetido y no conserva campos adicionales", () => {
    expect(() =>
      decodePage(
        { items: [row], next_after: null },
        200,
        headers(),
        actor,
        target,
      ),
    ).toThrow();
    const page = decodePage(
      { items: [{ ...row, password_hash: "canary" }], next_after: null },
      200,
      headers(),
      actor,
    );
    expect(page.items[0]).not.toHaveProperty("password_hash");
    expect(
      decodeDetail(
        { ...member, token: "canary" },
        200,
        headers(),
        actor,
        target,
      ),
    ).not.toHaveProperty("token");
  });
  it("rechaza detalle sin ETag o con objetivo distinto", () => {
    const meta = headers();
    meta.delete("ETag");
    expect(() => decodeDetail(member, 200, meta, actor, target)).toThrow();
    expect(() => decodeDetail(member, 200, headers(), actor, actor)).toThrow();
  });
  it("no escribe sin prueba CSRF ni acepta IDs/rutas arbitrarias", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      membershipsApi.change(
        actor,
        target,
        { role: "mechanic", is_active: true },
        etag,
      ),
    ).rejects.toMatchObject({ kind: "csrf" });
    expect(fetch).toHaveBeenCalledOnce();
    expect(() => membershipsApi.detail(actor, "../elsewhere")).toThrow();
  });
});
