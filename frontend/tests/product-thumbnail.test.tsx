import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    false,
    true,
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

it("conserva la foto al recuperar foco y la renueva cuando cambia el producto", async () => {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 88,
    height: 88,
    top: 0,
    left: 0,
    bottom: 88,
    right: 88,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
  const read = vi
    .spyOn(productImages, "read")
    .mockResolvedValue(new Blob(["image"]));
  render(
    <ProductThumbnail
      actor={actor}
      productId={target}
      name="Filtro"
      onProblem={vi.fn()}
    />,
  );
  await screen.findByRole("img", { name: "Imagen de Filtro" });
  fireEvent.focus(window);
  fireEvent(window, new Event("pageshow"));
  fireEvent(document, new Event("visibilitychange"));
  expect(read).toHaveBeenCalledTimes(1);
  fireEvent(
    window,
    new CustomEvent("carrillo_product_photo_changed", {
      detail: { actor, productId: target },
    }),
  );
  await waitFor(() => expect(read).toHaveBeenCalledTimes(2));
});
