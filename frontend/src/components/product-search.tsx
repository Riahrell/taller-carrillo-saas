"use client";

import { useEffect, useState, type FormEvent } from "react";
import {
  catalogApi,
  productSearchInput,
  type Category,
} from "@/lib/catalog/api";

import { availabilityApi } from "@/lib/inventory/availability";

type Props = {
  operational?: boolean;
  actor: string;
  value: string;
  category: string | null;
  disabled: boolean;
  onSearch: (value: string, category: string | null) => void;
};
type Categories =
  | { actor: string; kind: "ready"; items: Category[] }
  | { actor: string; kind: "error" };
export default function ProductSearch({
  operational = false,
  actor,
  value,
  category,
  disabled,
  onSearch,
}: Props) {
  const [input, setInput] = useState({ value, text: value });
  const draft = input.value === value ? input.text : value;
  const setDraft = (text: string) => setInput({ value, text });
  const [error, setError] = useState("");
  const [categories, setCategories] = useState<Categories | null>(null);
  const [attempt, setAttempt] = useState(0);
  const own = categories?.actor === actor ? categories : null;
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      if (active) setCategories({ actor, kind: "error" });
    }, 30_000);
    async function load() {
      const items: Category[] = [];
      let after: string | undefined;
      do {
        const page = await (
          operational ? availabilityApi : catalogApi
        ).categories(actor, after, {
          signal: controller.signal,
        });
        if (controller.signal.aborted) return;
        items.push(...page.items);
        if (page.next_after && after && page.next_after <= after)
          throw new Error("Nonadvancing categories");
        after = page.next_after ?? undefined;
      } while (after);
      if (active)
        setCategories({
          actor,
          kind: "ready",
          items: items.sort((a, b) => a.name.localeCompare(b.name, "es")),
        });
    }
    void load()
      .catch(() => {
        if (active && !controller.signal.aborted)
          setCategories({ actor, kind: "error" });
      })
      .finally(() => clearTimeout(timer));
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [actor, attempt, operational]);
  function apply(nextCategory: string | null) {
    if (disabled) return;
    try {
      const text = productSearchInput(draft);
      setDraft(text);
      setError("");
      onSearch(text, nextCategory);
    } catch {
      setError("Revisa la búsqueda. Puedes escribir hasta 200 caracteres.");
    }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    apply(category);
  }
  return (
    <form
      role="search"
      aria-label="Buscar productos"
      className="product-search"
      onSubmit={submit}
    >
      <label>
        Buscar producto por nombre o SKU
        <input
          type="search"
          autoComplete="off"
          maxLength={200}
          value={draft}
          disabled={disabled}
          onChange={(event) => setDraft(event.target.value)}
        />
      </label>
      <label>
        Filtrar por categoría
        <select
          value={category ?? ""}
          disabled={disabled || own?.kind !== "ready"}
          onChange={(event) => apply(event.target.value || null)}
        >
          <option value="">Todas las categorías</option>
          {category &&
            (own?.kind !== "ready" ||
              !own.items.some((item) => item.id === category)) && (
              <option value={category}>Categoría seleccionada</option>
            )}
          {own?.kind === "ready" &&
            own.items.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.is_active ? "" : " (inactiva)"}
              </option>
            ))}
        </select>
      </label>
      {!own && <p role="status">Consultando categorías…</p>}
      {own?.kind === "error" && (
        <p role="alert">
          No pudimos cargar las categorías. Puedes seguir buscando por nombre o
          SKU.
          <button
            type="button"
            className="text-action"
            disabled={disabled}
            onClick={() => {
              setCategories(null);
              setAttempt((value) => value + 1);
            }}
          >
            Reintentar categorías
          </button>
        </p>
      )}
      <div className="catalog-tabs">
        <button className="action primary" disabled={disabled}>
          Buscar
        </button>
        <button
          className="text-action"
          type="button"
          disabled={disabled || (!draft && !value && !category)}
          onClick={() => {
            setDraft("");
            setError("");
            onSearch("", null);
          }}
        >
          Limpiar búsqueda
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
