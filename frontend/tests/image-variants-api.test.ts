import { expect, it, vi } from "vitest";
import { productImages } from "@/lib/catalog/images";
const actor = "00000000-0000-4000-8000-000000000001";
const product = "00000000-0000-4000-8000-000000000002";
const encoded =
  "UklGRjwAAABXRUJQVlA4IDAAAADQAQCdASoIAAQAAUAiJaACdLoB+AADsAD+899n/n5nduL+q3/7DP+vMvrzL/sFgAA=";
function photo() {
  return Uint8Array.from(atob(encoded), (value) => value.charCodeAt(0));
}
it("pide la miniatura privada por el contexto administrativo u operativo y acepta WebP real", async () => {
  for (const operational of [false, true]) {
    const fetch = vi.fn().mockResolvedValue(
      new Response(photo(), {
        status: 200,
        headers: {
          "Content-Type": "image/webp",
          "Content-Length": String(photo().length),
          "X-Access-Membership": actor,
          "X-Image-Id": product,
        },
      }),
    );
    vi.stubGlobal("fetch", fetch);
    const image = await productImages.read(
      actor,
      product,
      undefined,
      operational,
      true,
    );
    expect(image?.type).toBe("image/webp");
    expect(fetch.mock.calls[0][0]).toBe(
      operational
        ? `/api/inventory/availability/products/${product}/image/thumbnail`
        : `/api/catalog/products/${product}/image/thumbnail`,
    );
    expect(fetch.mock.calls[0][1].cache).toBe("no-store");
    expect(fetch.mock.calls[0][1].headers["X-Expected-Membership"]).toBe(actor);
  }
});
it("rechaza WebP con firma falsa, longitud inválida o miniatura excesiva", async () => {
  for (const [payload, length] of [
    [new Uint8Array(photo().length), photo().length],
    [photo(), photo().length + 1],
    [photo(), 262145],
  ] as const) {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(payload, {
          status: 200,
          headers: {
            "Content-Type": "image/webp",
            "Content-Length": String(length),
            "X-Access-Membership": actor,
            "X-Image-Id": product,
          },
        }),
      ),
    );
    await expect(
      productImages.read(actor, product, undefined, false, true),
    ).rejects.toMatchObject({ kind: "invalid-response" });
  }
});
