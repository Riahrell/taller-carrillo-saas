import { afterEach, expect, it, vi } from "vitest";
import {
  catalogApi,
  decodePage,
  decodeProduct,
  displayPrice,
  priceInput,
} from "@/lib/catalog/api";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const product = {
  id: target,
  name: "Filtro",
  is_active: true,
  sku: null,
  category_id: null,
  price: "12.34",
};
const headers = () => new Headers({ "X-Access-Membership": actor });
const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
afterEach(() => {
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});

it("mantiene todos los dígitos de un precio grande sin usar float", () => {
  expect(priceInput("999999999999999999,99")).toBe("999999999999999999.99");
  expect(displayPrice("999999999999999999.99")).toBe(
    "999,999,999,999,999,999.99 USD",
  );
  expect(priceInput("0")).toBe("0.00");
  expect(priceInput("12,3")).toBe("12.30");
});
it.each([
  "-1",
  "1.001",
  "NaN",
  "Infinity",
  "1e3",
  "1,000.00",
  "1000000000000000000",
  "",
  "01",
])("rechaza precio ambiguo o fuera de límites: %s", (value) => {
  expect(() => priceInput(value)).toThrow();
});
it("consulta una página acotada sin enviar tenant ni autoridad", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      Response.json(
        { items: [product], next_after: null },
        { headers: headers() },
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await expect(catalogApi.products(actor)).resolves.toEqual({
    items: [product],
    next_after: null,
  });
  expect(fetch.mock.calls[0][0]).toBe("/api/catalog/products?limit=25");
  expect(fetch.mock.calls[0][1]).toMatchObject({
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
  });
});
it("rechaza campos privados, precios numéricos y respuesta de otro contexto", () => {
  expect(() => decodeProduct({ ...product, cost: "1.00" })).toThrow();
  expect(() => decodeProduct({ ...product, price: 12.34 })).toThrow();
  expect(() =>
    decodePage(
      { items: [product], next_after: null },
      200,
      new Headers({ "X-Access-Membership": target }),
      actor,
      decodeProduct,
    ),
  ).toThrow();
});
it("rechaza cursores incoherentes, páginas excesivas y elementos repetidos", () => {
  for (const page of [
    { items: [product], next_after: target },
    { items: [product, product], next_after: null },
    { items: Array(26).fill(product), next_after: null },
  ]) {
    expect(() =>
      decodePage(page, 200, headers(), actor, decodeProduct),
    ).toThrow();
  }
});
it("renueva CSRF y manda solo los campos administrativos conocidos, con precio string", async () => {
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(
      Response.json(product, { status: 201, headers: headers() }),
    );
  vi.stubGlobal("fetch", fetch);
  const data = {
    name: "Filtro",
    price: "12,34",
    sku: null,
    category_id: null,
    tenant_id: target,
    stock: "99",
  };
  await catalogApi.createProduct(actor, data);
  expect(fetch.mock.calls[0][0]).toBe("/api/auth/csrf");
  const [path, init] = fetch.mock.calls[1];
  expect(path).toBe("/api/catalog/products");
  expect(init.headers).toMatchObject({
    "X-CSRF-Token": proof,
    "X-Expected-Membership": actor,
  });
  expect(JSON.parse(init.body)).toEqual({
    name: "Filtro",
    price: "12.34",
    sku: null,
    category_id: null,
  });
});
it.each([401, 403, 409, 422, 428, 503])(
  "no repite el POST tras %s ni refleja errores privados",
  async (status) => {
    document.cookie = `carrillo_csrf=${proof}; Path=/`;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response("private-canary", { status }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      catalogApi.createCategory(actor, "Repuestos"),
    ).rejects.toMatchObject({ status });
    expect(fetch).toHaveBeenCalledTimes(2);
  },
);
