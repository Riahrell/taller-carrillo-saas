import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import ProductThumbnail from "@/components/product-thumbnail";
import { productImages } from "@/lib/catalog/images";
import { actor, target } from "./inventory-data";

const OriginalURL = URL;
const create = vi.fn(() => "blob:private-product");
const revoke = vi.fn();
beforeEach(() => {
  create.mockClear();
  revoke.mockClear();
  vi.stubGlobal("IntersectionObserver", undefined);
  vi.stubGlobal(
    "URL",
    class extends OriginalURL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
});
it("muestra la imagen privada del producto en catálogo y libera el blob al salir", async () => {
  vi.spyOn(productImages, "read").mockResolvedValue(
    new Blob(["synthetic PNG"], { type: "image/png" }),
  );
  const view = render(
    <ProductThumbnail
      actor={actor}
      productId={target}
      name="Filtro"
      onProblem={vi.fn()}
    />,
  );
  expect(
    await screen.findByRole("img", { name: "Imagen de Filtro" }),
  ).toHaveAttribute("src", "blob:private-product");
  expect(productImages.read).toHaveBeenCalledWith(
    actor,
    target,
    expect.any(AbortSignal),
  );
  view.unmount();
  expect(revoke).toHaveBeenCalledWith("blob:private-product");
});
it("no crea URLs con respuestas tardías después de perder el contexto", async () => {
  let complete!: (value: Blob | null) => void;
  vi.spyOn(productImages, "read").mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  const view = render(
    <ProductThumbnail
      actor={actor}
      productId={target}
      name="Filtro"
      onProblem={vi.fn()}
    />,
  );
  await waitFor(() => expect(productImages.read).toHaveBeenCalled());
  const signal = vi.mocked(productImages.read).mock.calls[0][2]!;
  view.unmount();
  expect(signal.aborted).toBe(true);
  complete(new Blob(["late"]));
  await Promise.resolve();
  expect(create).not.toHaveBeenCalled();
});
