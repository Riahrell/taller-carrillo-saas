import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import ProductSearch from "@/components/product-search";
import { catalogApi, type Category, type Page } from "@/lib/catalog/api";

const actor = "00000000-0000-4000-8000-000000000001";
const id = (value: number) =>
  `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const category = (n: number): Category => ({
  id: id(n),
  name: `Categoría ${n}`,
  is_active: true,
});

it("incluye categorías posteriores a la primera página", async () => {
  const onSearch = vi.fn();
  vi.spyOn(catalogApi, "categories").mockImplementation(async (_, after) =>
    after
      ? { items: [category(26)], next_after: null }
      : {
          items: Array.from({ length: 25 }, (_, n) => category(n + 1)),
          next_after: id(25),
        },
  );
  render(
    <ProductSearch
      actor={actor}
      value="aceite"
      category={null}
      disabled={false}
      onSearch={onSearch}
    />,
  );
  expect(
    await screen.findByRole("option", { name: "Categoría 26" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filtrar por categoría"), {
    target: { value: id(26) },
  });
  expect(onSearch).toHaveBeenCalledWith("aceite", id(26));
  expect(catalogApi.categories).toHaveBeenCalledTimes(2);
});

it("un fallo de categorías mantiene el filtro aplicado y permite reintentar", async () => {
  const onSearch = vi.fn();
  vi.spyOn(catalogApi, "categories")
    .mockRejectedValueOnce(new Error("unavailable"))
    .mockResolvedValue({ items: [category(7)], next_after: null });
  render(
    <ProductSearch
      actor={actor}
      value="filtro"
      category={id(7)}
      disabled={false}
      onSearch={onSearch}
    />,
  );
  const retry = await screen.findByRole("button", {
    name: "Reintentar categorías",
  });
  fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
  expect(onSearch).toHaveBeenLastCalledWith("filtro", id(7));
  fireEvent.click(retry);
  expect(
    await screen.findByRole("option", { name: "Categoría 7" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Limpiar búsqueda" }));
  expect(onSearch).toHaveBeenLastCalledWith("", null);
});

it("descarta categorías tardías de una membresía anterior", async () => {
  let finish!: (page: Page<Category>) => void;
  const other = id(99);
  vi.spyOn(catalogApi, "categories").mockImplementation(async (membership) =>
    membership === actor
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : { items: [category(8)], next_after: null },
  );
  const props = {
    value: "",
    category: null,
    disabled: false,
    onSearch: vi.fn(),
  };
  const view = render(<ProductSearch actor={actor} {...props} />);
  view.rerender(<ProductSearch actor={other} {...props} />);
  expect(
    await screen.findByRole("option", { name: "Categoría 8" }),
  ).toBeInTheDocument();
  await act(async () => finish({ items: [category(9)], next_after: null }));
  expect(
    screen.queryByRole("option", { name: "Categoría 9" }),
  ).not.toBeInTheDocument();
});
