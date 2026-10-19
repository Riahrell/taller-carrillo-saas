import { afterEach, expect, it, vi } from "vitest";
import { accessApi } from "@/lib/access/api";
import { decodeImageReceipt, productImages } from "@/lib/catalog/images";
import { actor, target } from "./inventory-data";

const receipt = {
  id: target,
  product_id: target,
  width: 3,
  height: 2,
  size: 100,
  created_at: "2026-10-18T12:00:00Z",
  is_current: true,
};
afterEach(() => {
  document.cookie = "carrillo_csrf=; Max-Age=0; Path=/";
});
it("rechaza campos de autoridad y metadatos fuera de límites", () => {
  expect(decodeImageReceipt(receipt, target).id).toBe(target);
  for (const changes of [
    { tenant_id: target },
    { width: 1601 },
    { height: 0 },
    { size: 12582913 },
    { id: target + "\n" },
    { product_id: actor },
  ])
    expect(() =>
      decodeImageReceipt({ ...receipt, ...changes }, target),
    ).toThrow();
});
it("envía bytes con CSRF, contexto y misma operación sin aceptar tenant o key S3", async () => {
  const proof = `v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}`;
  document.cookie = `carrillo_csrf=${proof}; Path=/`;
  vi.spyOn(accessApi, "prepare").mockResolvedValue(undefined);
  const fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(receipt), {
      status: 200,
      headers: { "X-Access-Membership": actor },
    }),
  );
  vi.stubGlobal("fetch", fetch);
  const file = new File(["bytes"], "private.png");
  await productImages.upload(actor, { product_id: target, key: target, file });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe(`/api/catalog/products/${target}/image`);
  expect(fetch.mock.calls[0][1].body).toBe(file);
  expect(fetch.mock.calls[0][1].headers["Idempotency-Key"]).toBe(target);
  expect(fetch.mock.calls[0][1].headers["X-CSRF-Token"]).toBe(proof);
});
it("no reintenta una escritura si se pierde la respuesta", async () => {
  vi.spyOn(accessApi, "prepare").mockResolvedValue(undefined);
  document.cookie = `carrillo_csrf=v1.1999999999.${"a".repeat(43)}.${"b".repeat(64)}; Path=/`;
  const fetch = vi.fn().mockRejectedValue(new TypeError("network"));
  vi.stubGlobal("fetch", fetch);
  await expect(
    productImages.upload(actor, {
      product_id: target,
      key: target,
      file: new File(["bytes"], "private.png"),
    }),
  ).rejects.toMatchObject({ kind: "network" });
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("una lectura exige el actor real y tipo PNG verificados", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response("invalid", {
        status: 200,
        headers: {
          "X-Access-Membership": actor,
          "X-Image-Id": target,
          "Content-Type": "text/html",
        },
      }),
    ),
  );
  await expect(productImages.read(actor, target)).rejects.toMatchObject({
    kind: "invalid-response",
  });
});

it("limita bytes y admite solo una imagen PNG con contexto válido", async () => {
  const content = new Uint8Array(33);
  content.set([137, 80, 78, 71, 13, 10, 26, 10]);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(content, {
        status: 200,
        headers: {
          "X-Access-Membership": actor,
          "X-Image-Id": target,
          "Content-Type": "image/png",
          "Content-Length": "33",
        },
      }),
    ),
  );
  const blob = await productImages.read(actor, target);
  expect(blob?.type).toBe("image/png");
  expect(blob?.size).toBe(33);
});

it("serializa lecturas hasta terminar el cuerpo para no saturar fotos del catálogo", async () => {
  const png = new Uint8Array(33);
  png.set([137, 80, 78, 71, 13, 10, 26, 10]);
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(value) {
      controller = value;
    },
  });
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      new Response(stream, {
        status: 200,
        headers: {
          "X-Access-Membership": actor,
          "X-Image-Id": target,
          "Content-Type": "image/png",
          "Content-Length": "33",
        },
      }),
    )
    .mockResolvedValueOnce(
      new Response(null, {
        status: 204,
        headers: { "X-Access-Membership": actor },
      }),
    );
  vi.stubGlobal("fetch", fetch);
  const first = productImages.read(actor, target);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  const second = productImages.read(actor, target);
  await Promise.resolve();
  expect(fetch).toHaveBeenCalledTimes(1);
  controller.enqueue(png);
  controller.close();
  expect((await first)?.size).toBe(33);
  expect(await second).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(2);
});
