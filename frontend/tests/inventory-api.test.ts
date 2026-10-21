import { afterEach, expect, it, vi } from "vitest";
import {
  inventoryApi,
  decodeBalance,
  decodeCount,
  quantityInput,
  costInput,
  displayDecimal,
} from "@/lib/inventory/api";
import {
  actor,
  target,
  empty,
  command,
  evidence,
  historyEvidence as historyRow,
} from "./inventory-data";

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
  } as unknown as typeof command);
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
it("registra entrada con costo explícito aunque ya haya existencias", async () => {
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  const key = command.key;
  const result = {
    ...evidence,
    direction: "IN",
    before_quantity: "2.000",
    before_value: "6.000000",
    after_quantity: "4.000",
    after_value: "16.000000",
    value_delta: "10.000000",
    declared_unit_cost: "5.000000",
  };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json(result, { headers: headers() }));
  vi.stubGlobal("fetch", fetch);
  await inventoryApi.record(actor, {
    key,
    data: {
      product_id: target,
      direction: "IN",
      quantity: "2",
      reason: evidence.reason,
      unit_cost: "5",
    },
  });
  expect(fetch.mock.calls[1][0]).toBe("/api/inventory/movements");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    product_id: target,
    direction: "IN",
    quantity: "2.000",
    reason: evidence.reason,
    unit_cost: "5.000000",
  });
});

it("combina orígenes con la misma fecha y UUID sin perder registros", async () => {
  const rows = [
    {
      ...historyRow,
      source: "RECEIPT",
      source_document_id: actor,
      reason: null,
    },
    { ...historyRow, source: "MANUAL", direction: "IN" },
    historyRow,
  ];
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: rows, next_after: null },
          { headers: headers() },
        ),
      ),
  );
  await expect(inventoryApi.history(actor, target)).resolves.toEqual({
    items: rows,
    next_after: null,
  });
});
it.each([
  { product_id: actor },
  { quantity_delta: "1.000" },
  { source: "SALE" },
  { source: "RECEIPT" },
  { direction: "OUT" },
  { recorded_by: "" },
  { source_document_id: actor },
  { reason: null },
  { tenant_id: actor },
])("rechaza historial incoherente: %o", async (change) => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: [{ ...historyRow, ...change }], next_after: null },
          { headers: headers() },
        ),
      ),
  );
  await expect(inventoryApi.history(actor, target)).rejects.toMatchObject({
    kind: "invalid-response",
  });
});
it("liga el cursor al tipo de origen y rechaza páginas repetidas", async () => {
  const after = { ...historyRow, source: "COUNT" as const, direction: null };
  const fetch = vi
    .fn()
    .mockResolvedValue(
      Response.json(
        { items: [after], next_after: null },
        { headers: headers() },
      ),
    );
  vi.stubGlobal("fetch", fetch);
  await expect(
    inventoryApi.history(actor, target, after),
  ).rejects.toMatchObject({ kind: "invalid-response" });
  expect(String(fetch.mock.calls[0][0])).toContain(
    `after=COUNT%3A${evidence.id}`,
  );
});

it("acepta una página completa y su cursor de origen sin duplicar la frontera", async () => {
  const items = Array.from({ length: 25 }, (_, index) => {
    const id = `00000000-0000-4000-8000-${String(25 - index).padStart(12, "0")}`;
    return { ...historyRow, id, source_document_id: id };
  });
  const last = items.at(-1)!;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json(
        { items, next_after: `COUNT:${last.id}` },
        { headers: headers() },
      ),
    )
    .mockResolvedValueOnce(
      Response.json({ items: [], next_after: null }, { headers: headers() }),
    );
  vi.stubGlobal("fetch", fetch);
  const page = await inventoryApi.history(actor, target);
  expect(page.items).toHaveLength(25);
  await expect(
    inventoryApi.history(actor, target, page.items.at(-1)),
  ).resolves.toEqual({ items: [], next_after: null });
  expect(String(fetch.mock.calls[1][0])).toContain(`after=COUNT%3A${last.id}`);
});

it("envía una reversa sin cantidades, costos ni saldos del cliente", async () => {
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  const key = command.key;
  const result = {
    id: actor,
    product_id: target,
    operation_key: key,
    original_source: "COUNT",
    original_id: evidence.id,
    reason: "Corrección",
    before_quantity: "2.000",
    after_quantity: "0.000",
    before_value: "6.000000",
    after_value: "0.000000",
    quantity_delta: "-2.000",
    value_delta: "-6.000000",
    created_at: evidence.created_at,
  };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json(result, { headers: headers() }));
  vi.stubGlobal("fetch", fetch);
  const data = {
    product_id: target,
    source: "COUNT" as const,
    original_id: evidence.id,
    reason: "Corrección",
    quantity: "999",
    price: "0.01",
  };
  await expect(inventoryApi.record(actor, { key, data })).resolves.toEqual(
    result,
  );
  expect(fetch.mock.calls[1][0]).toBe("/api/inventory/reversals");
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({
    product_id: target,
    source: "COUNT",
    original_id: evidence.id,
    reason: "Corrección",
  });
  expect(fetch.mock.calls[1][1].headers["Idempotency-Key"]).toBe(key);
});
