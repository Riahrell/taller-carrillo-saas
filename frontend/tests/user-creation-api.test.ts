import { afterEach, describe, expect, it, vi } from "vitest";
import { membershipsApi } from "@/lib/memberships/api";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
const etag = `"membership-v1-${"a".repeat(64)}"`;
const desired = {
  email: "new@example.com",
  role: "mechanic" as const,
  password: " 🔧" + "a".repeat(15) + " ",
};
const result = { membership_id: target, role: "mechanic", is_active: true };
function response(changes = {}, headers = {}, status = 201) {
  return Response.json(
    { ...result, ...changes },
    {
      status,
      headers: {
        "X-Access-Membership": actor,
        ETag: etag,
        Location: `./memberships/${target}`,
        ...headers,
      },
    },
  );
}
function setup(reply: Response) {
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(reply);
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => {
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});
describe("alta administrativa por API", () => {
  it("renueva CSRF y envía solo email/password/role con contexto y sin If-Match", async () => {
    const fetch = setup(response());
    await expect(
      membershipsApi.create(actor, {
        ...desired,
        tenant_id: "bad",
        confirmation: "bad",
      } as typeof desired),
    ).resolves.toEqual({ ...result, etag });
    expect(fetch.mock.calls[0][0]).toBe("/api/auth/csrf");
    const [path, init] = fetch.mock.calls[1];
    expect(path).toBe("/api/auth/memberships");
    expect(init).toMatchObject({
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      redirect: "error",
      headers: { "X-CSRF-Token": proof, "X-Expected-Membership": actor },
    });
    expect(JSON.parse(init.body)).toEqual(desired);
    for (const key of ["Origin", "Cookie", "If-Match"])
      expect(init.headers).not.toHaveProperty(key);
  });
  it.each([401, 403, 409, 422, 428, 429, 503])(
    "no repite el alta ni muestra cuerpo privado tras %s",
    async (status) => {
      const fetch = setup(new Response("private-canary", { status }));
      await expect(membershipsApi.create(actor, desired)).rejects.toMatchObject(
        { status, message: "No se pudo completar la solicitud." },
      );
      expect(fetch).toHaveBeenCalledTimes(2);
    },
  );
  it.each([
    [{}, {}, 200],
    [{}, { "X-Access-Membership": target }, 201],
    [{}, { ETag: "bad" }, 201],
    [{}, { Location: "https://foreign.example/" }, 201],
    [{ role: "admin" }, {}, 201],
    [{ is_active: false }, {}, 201],
    [{ membership_id: actor }, {}, 201],
    [{ membership_id: "../elsewhere" }, {}, 201],
  ] as const)(
    "descarta estado, actor o representación no correspondientes",
    async (body, headers, status) => {
      setup(response(body, headers, status));
      await expect(membershipsApi.create(actor, desired)).rejects.toMatchObject(
        { kind: "invalid-response" },
      );
    },
  );
  it("no conserva campos extra de respuesta", async () => {
    setup(response({ password_hash: "canary", token: "canary" }));
    expect(await membershipsApi.create(actor, desired)).toEqual({
      ...result,
      etag,
    });
  });
  it("rechaza credenciales inválidas antes de llamar a red", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(
      membershipsApi.create(actor, { ...desired, password: "x".repeat(14) }),
    ).rejects.toThrow();
    await expect(membershipsApi.create("../bad", desired)).rejects.toThrow();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("sin prueba CSRF no envía contraseña", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await expect(membershipsApi.create(actor, desired)).rejects.toMatchObject({
      kind: "csrf",
    });
    expect(fetch).toHaveBeenCalledOnce();
  });
});
