import { expect, it, vi } from "vitest";
import { availabilityApi } from "@/lib/inventory/availability";
import { productImages } from "@/lib/catalog/images";
import { permissionsApi } from "@/lib/employees/permissions";
import { accessApi } from "@/lib/access/api";
const actor = "00000000-0000-4000-8000-000000000001",
  target = "00000000-0000-4000-8000-000000000002";
const item = {
  id: target,
  name: "Filtro",
  sku: null,
  category: null,
  quantity: "1.000",
  price: "2.00",
};
it("rechaza costos inesperados o actor distinto incluso con respuesta HTTP exitosa", async () => {
  for (const [data, headers] of [
    [{ ...item, value: "3.000000" }, { "X-Access-Membership": actor }],
    [item, { "X-Access-Membership": target }],
  ] as const) {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ items: [data], next_after: null }), {
          status: 200,
          headers,
        }),
      ),
    );
    await expect(
      availabilityApi.products(actor, { q: "", category: null }),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  }
});
it("consulta fotos por el endpoint operativo sin abrir el catálogo administrativo", async () => {
  const bytes = new Uint8Array(33);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
  const fetch = vi.fn().mockResolvedValue(
    new Response(bytes, {
      status: 200,
      headers: {
        "X-Access-Membership": actor,
        "X-Image-Id": target,
        "Content-Type": "image/png",
        "Content-Length": "33",
      },
    }),
  );
  vi.stubGlobal("fetch", fetch);
  expect(
    await productImages.read(actor, target, undefined, true),
  ).toBeInstanceOf(Blob);
  expect(fetch.mock.calls[0][0]).toBe(
    `/api/inventory/availability/products/${target}/image`,
  );
});
it("el cambio de permiso envía solo el deseo, con CSRF, contexto y versión", async () => {
  const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  vi.spyOn(accessApi, "prepare").mockResolvedValue(undefined);
  const etag = `"inventory-read-v1-${target}-1"`;
  const fetch = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        membership_id: target,
        granted: true,
        eligible: true,
        effective: true,
        revision: "1",
      }),
      { status: 200, headers: { "X-Access-Membership": actor, ETag: etag } },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  await permissionsApi.change(
    actor,
    target,
    true,
    `"inventory-read-v1-${target}-0"`,
  );
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ granted: true });
  expect(fetch.mock.calls[0][1].headers["X-Expected-Membership"]).toBe(actor);
  expect(fetch.mock.calls[0][1].headers["If-Match"]).toBe(
    `"inventory-read-v1-${target}-0"`,
  );
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});
