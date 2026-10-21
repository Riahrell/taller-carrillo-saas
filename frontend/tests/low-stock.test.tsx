import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import LowStockList from "@/components/low-stock-list";
import { lowStockApi } from "@/lib/inventory/low-stock";
import { productImages } from "@/lib/catalog/images";
import { actor, target } from "./inventory-data";
const row = {
  id: target,
  name: "Filtro",
  sku: null,
  quantity: "0.000",
  minimum: "1.000",
};
beforeEach(() => {
  vi.spyOn(productImages, "read").mockResolvedValue(null);
});
it("consulta a demanda, abre la ficha y actualiza los avisos", async () => {
  const list = vi
    .spyOn(lowStockApi, "list")
    .mockResolvedValue({ items: [row], next_after: null });
  const select = vi.fn();
  render(
    <LowStockList
      actor={actor}
      disabled={false}
      onProblem={vi.fn()}
      onSelect={select}
    />,
  );
  expect(list).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Ver productos por reponer" }),
  );
  await screen.findByRole("heading", { name: "Filtro" });
  fireEvent.click(
    screen.getByRole("button", { name: "Abrir inventario de Filtro" }),
  );
  expect(select).toHaveBeenCalledWith(target);
  fireEvent.click(screen.getByRole("button", { name: "Actualizar avisos" }));
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
});
it("no presenta un resultado vacío como una configuración automática", async () => {
  vi.spyOn(lowStockApi, "list").mockResolvedValue({
    items: [],
    next_after: null,
  });
  render(
    <LowStockList
      actor={actor}
      disabled={false}
      onProblem={vi.fn()}
      onSelect={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Ver productos por reponer" }),
  );
  await screen.findByText(
    /No hay productos por reponer con los mínimos configurados/,
  );
});
it("valida cantidades, empresa y orden de la respuesta", async () => {
  const headers = { "X-Access-Membership": actor };
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json({ items: [row], next_after: null }, { headers }),
      ),
  );
  await expect(lowStockApi.list(actor)).resolves.toMatchObject({
    items: [row],
  });
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        Response.json(
          { items: [{ ...row, quantity: "2.000" }], next_after: null },
          { headers },
        ),
      ),
  );
  await expect(lowStockApi.list(actor)).rejects.toMatchObject({
    kind: "invalid-response",
  });
});
