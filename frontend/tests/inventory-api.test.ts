import { afterEach, expect, it, vi } from "vitest";
import {
  inventoryApi,
  decodeBalance,
  decodeCount,
  quantityInput,
  costInput,
  displayDecimal,
} from "@/lib/inventory/api";
import { actor, target, empty, command, evidence } from "./inventory-data";

const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
const headers = () => new Headers({ "X-Access-Membership": actor });
afterEach(() => {
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});
it("conserva máximos de cantidad/costo y muestra sin pérdida por float", () => {
  expect(quantityInput("999999999999999,999")).toBe("999999999999999.999");
  expect(costInput("9999999999999999999999.999999")).toBe(
    "9999999999999999999999.999999",
  );
  expect(displayDecimal("9999999999999999999999.999999", 2)).toBe(
    "9,999,999,999,999,999,999,999.999999",
  );
  expect(displayDecimal("3.000000", 2)).toBe("3.00");
});
it.each(["1.0000", "NaN", "-1", "1e3", "01", "1000000000000000", "1,000.1"])(
  "rechaza cantidad no exacta: %s",
  (value) => {
    expect(() => quantityInput(value)).toThrow();
  },
);
it("comprueba promedio HALF_UP con enteros exactos y distingue stock vacío", () => {
  expect(decodeBalance(empty, target)).toEqual(empty);
  expect(
    decodeBalance(
      {
        ...empty,
        quantity: "0.003",
        value: "0.000002",
        average_unit_cost: "0.000667",
      },
      target,
    ).average_unit_cost,
  ).toBe("0.000667");
  expect(() =>
    decodeBalance(
      {
        ...empty,
        quantity: "0.003",
        value: "0.000002",
        average_unit_cost: "0.000666",
      },
      target,
    ),
  ).toThrow();
});
it.each([
  { tenant_id: target },
  { product_id: actor },
  { quantity: 0 },
  { quantity: "0.000\n" },
  { average_unit_cost: "0.000000" },
  { value: "1.000000" },
])("rechaza saldo incoherente o campos privados: %o", (change) => {
  expect(() => decodeBalance({ ...empty, ...change }, target)).toThrow();
});
it("comprueba diferencias sin perder los seis decimales", () => {
  expect(decodeCount(evidence, target)).toEqual(evidence);
  expect(() =>
    decodeCount({ ...evidence, value_delta: "6.000001" }, target),
  ).toThrow();
  expect(() =>
    decodeCount({ ...evidence, before_value: "0.000001" }, target),
  ).toThrow();
});
it.each([
  { request_fingerprint: "private" },
  { reason: 123 },
  { created_at: "2026-02-31T20:00:00Z" },
  { declared_unit_cost: null },
  { reason: " invalid " },
])("rechaza evidencia inválida: %o", (change) => {
  expect(() => decodeCount({ ...evidence, ...change }, target)).toThrow();
});
it("envía solo el conteo y conserva clave y autoridad separadas del JSON", async () => {
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json(evidence, { headers: headers() }));
  vi.stubGlobal("fetch", fetch);
  await inventoryApi.record(actor, {
    ...command,
    data: { ...command.data, tenant_id: target, price: "0.01" },
  } as typeof command);
  const [path, init] = fetch.mock.calls[1];
  expect(path).toBe("/api/inventory/counts");
  expect(init.headers).toMatchObject({
    "X-Expected-Membership": actor,
    "Idempotency-Key": command.key,
    "X-CSRF-Token": proof,
  });
  expect(JSON.parse(init.body)).toEqual(command.data);
  expect(init).toMatchObject({
    cache: "no-store",
    credentials: "same-origin",
    redirect: "error",
  });
});
it.each([401, 403, 409, 422, 428, 503])(
  "no reintenta automáticamente el POST tras %s",
  async (status) => {
    document.cookie = `carrillo_csrf=${proof}; Path=/`;
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response("private-canary", { status }));
    vi.stubGlobal("fetch", fetch);
    await expect(inventoryApi.record(actor, command)).rejects.toMatchObject({
      status,
    });
    expect(fetch).toHaveBeenCalledTimes(2);
  },
);
it("rechaza contexto y respuesta de otra operación antes de mostrarlos", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(empty, { headers: { "X-Access-Membership": target } }),
      ),
  );
  await expect(inventoryApi.balance(actor, target)).rejects.toMatchObject({
    kind: "invalid-response",
  });
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        Response.json(
          { ...evidence, operation_key: actor },
          { headers: headers() },
        ),
      ),
  );
  await expect(inventoryApi.record(actor, command)).rejects.toMatchObject({
    kind: "invalid-response",
  });
});
it("ordena el historial por microsegundos sin convertirlos a milisegundos", async () => {
  const newer = {
    ...evidence,
    id: actor,
    created_at: "2026-10-18T20:00:00.000002+00:00",
  };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: [newer, evidence], next_after: null },
          { headers: headers() },
        ),
      ),
  );
  await expect(inventoryApi.counts(actor, target)).resolves.toMatchObject({
    items: [newer, evidence],
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: [evidence, newer], next_after: null },
          { headers: headers() },
        ),
      ),
  );
  await expect(inventoryApi.counts(actor, target)).rejects.toMatchObject({
    kind: "invalid-response",
  });
});
