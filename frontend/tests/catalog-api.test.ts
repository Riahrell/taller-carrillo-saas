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
it("codifica la búsqueda como un único parámetro sin permitir inyectar empresa o fragmentos", async () => {
  const term = "XT%_\\&tenant_id=ajena?#";
  const fetch = vi
    .fn()
    .mockResolvedValue(
      Response.json(
        { items: [product], next_after: null },
        { headers: headers() },
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await catalogApi.products(actor, undefined, {}, term);
  const url = new URL(fetch.mock.calls[0][0], "https://same-origin.invalid");
  expect(url.searchParams.get("q")).toBe(term);
  expect(url.searchParams.has("tenant_id")).toBe(false);
  expect(url.hash).toBe("");
});
it("combina nombre y categoría sin enviar autoridad de empresa", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValue(
      Response.json(
        { items: [{ ...product, category_id: target }], next_after: null },
        { headers: headers() },
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await catalogApi.products(actor, undefined, {}, "filtro", target);
  const url = new URL(fetch.mock.calls[0][0], "https://same-origin.invalid");
  expect(url.searchParams.get("q")).toBe("filtro");
  expect(url.searchParams.get("category_id")).toBe(target);
  expect(url.searchParams.has("tenant_id")).toBe(false);
});
it("rechaza resultados que no pertenecen a la categoría consultada", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: [product], next_after: null },
          { headers: headers() },
        ),
      ),
  );
  await expect(
    catalogApi.products(actor, undefined, {}, "", target),
  ).rejects.toThrow();
});

it("edita con versión y contexto, enviando solo datos permitidos", async () => {
  const csrf = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
  document.cookie = `carrillo_csrf=${csrf}; Path=/`;
  const etag = `"product-v1-${target}-0"`,
    next = `"product-v1-${target}-1"`;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(
      Response.json(
        { ...product, is_active: false },
        { headers: new Headers({ "X-Access-Membership": actor, ETag: next }) },
      ),
    );
  vi.stubGlobal("fetch", fetch);
  const raw = {
    ...product,
    is_active: false,
    tenant_id: actor,
    quantity: "900",
  };
  await expect(
    catalogApi.updateProduct(actor, target, etag, raw),
  ).resolves.toMatchObject({ etag: next });
  expect(fetch.mock.calls[1][1].method).toBe("PATCH");
  expect(fetch.mock.calls[1][1].headers["If-Match"]).toBe(etag);
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    name: product.name,
    price: product.price,
    sku: null,
    category_id: null,
    is_active: false,
  });
});
it("exige una versión ligada al producto para editar", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json(product, { headers: headers() })),
  );
  await expect(catalogApi.editable(actor, target)).rejects.toMatchObject({
    kind: "invalid-response",
  });
  await expect(
    catalogApi.updateProduct(actor, target, `"product-v1-${actor}-0"`, {
      ...product,
    }),
  ).rejects.toThrow();
});
