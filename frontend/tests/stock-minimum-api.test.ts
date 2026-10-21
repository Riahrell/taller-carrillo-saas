import { expect, it, vi } from "vitest";
import { stockMinimumApi } from "@/lib/inventory/minimum";
import { actor, target } from "./inventory-data";
const body = {
  product_id: target,
  minimum: "0.000",
  quantity: "0.000",
  is_active: true,
  is_low: true,
};
const etag = `"stock-minimum-v1-${target}-1"`;
const headers = { "X-Access-Membership": actor, ETag: etag };
it("acepta el aviso de cero exacto y valores máximos sin float", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json(body, { headers })),
  );
  await expect(stockMinimumApi.read(actor, target)).resolves.toMatchObject({
    ...body,
    etag,
  });
});
it.each([
  { product_id: actor },
  { quantity: 0 },
  { minimum: "0.0001" },
  { minimum: null },
  { is_active: false },
  { tenant_id: actor },
])("rechaza respuesta incoherente o ajena: %o", async (change) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(Response.json({ ...body, ...change }, { headers })),
  );
  await expect(stockMinimumApi.read(actor, target)).rejects.toMatchObject({
    kind: "invalid-response",
  });
});
it("guarda solo el mínimo con versión y contexto", async () => {
  document.cookie = `carrillo_csrf=v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}; Path=/`;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json(body, { headers }));
  vi.stubGlobal("fetch", fetch);
  await stockMinimumApi.save(actor, target, etag, "0");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ minimum: "0.000" });
  expect(fetch.mock.calls[1][1].headers["If-Match"]).toBe(etag);
  expect(fetch.mock.calls[1][1].method).toBe("PATCH");
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});
