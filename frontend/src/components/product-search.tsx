"use client";

import { useState, type FormEvent } from "react";
import { productSearchInput } from "@/lib/catalog/api";

type Props = {
  value: string;
  disabled: boolean;
  onSearch: (value: string) => void;
};
export default function ProductSearch({ value, disabled, onSearch }: Props) {
  const [input, setInput] = useState({ value, text: value });
  const draft = input.value === value ? input.text : value;
  const setDraft = (text: string) => setInput({ value, text });
  const [error, setError] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    if (disabled) return;
    try {
      const text = productSearchInput(draft);
      setDraft(text);
      setError("");
      onSearch(text);
    } catch {
      setError("Revisa la búsqueda. Puedes escribir hasta 200 caracteres.");
    }
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
      <div className="catalog-tabs">
        <button className="action primary" disabled={disabled}>
          Buscar
        </button>
        <button
          className="text-action"
          type="button"
          disabled={disabled || (!draft && !value)}
          onClick={() => {
            setDraft("");
            setError("");
            onSearch("");
          }}
        >
          Limpiar búsqueda
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
