"use client";

import { useEffect, useId, useRef, useState } from "react";
import ProductThumbnail from "@/components/product-thumbnail";
import { lowStockApi, type LowStockItem } from "@/lib/inventory/low-stock";
import { displayDecimal } from "@/lib/inventory/api";
import type { Page } from "@/lib/catalog/api";

type Props = {
  actor: string;
  disabled: boolean;
  onProblem: (message: string) => void;
  onSelect: (id: string) => void;
};
export default function LowStockList({
  actor,
  disabled,
  onProblem,
  onSelect,
}: Props) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState<Page<LowStockItem> | null>(null);
  const [busy, setBusy] = useState(false);
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const panelId = useId();
  useEffect(() => () => controller.current?.abort(), [actor]);
  async function load(after?: string) {
    if (operation.current || disabled) return;
    operation.current = true;
    setBusy(true);
    const current = new AbortController();
    controller.current = current;
    try {
      const result = await lowStockApi.list(actor, after, {
        signal: current.signal,
      });
      if (!current.signal.aborted) setPage(result);
    } catch {
      if (!current.signal.aborted)
        onProblem(
          "No pudimos consultar los productos por reponer. Comprueba el acceso.",
        );
    } finally {
      operation.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section className="low-stock-panel" aria-label="Productos por reponer">
      <button
        className="action secondary"
        aria-expanded={expanded}
        aria-controls={panelId}
        disabled={disabled || busy}
        onClick={() => {
          if (!expanded) void load();
          setExpanded(!expanded);
        }}
      >
        {expanded
          ? "Ocultar productos por reponer"
          : "Ver productos por reponer"}
      </button>
      <div id={panelId} hidden={!expanded}>
        <h2>Productos por reponer</h2>
        <p>
          Productos activos de esta empresa con existencias iguales o inferiores
          al mínimo configurado. Esta lista es independiente del buscador.
        </p>
        {busy && <p role="status">Consultando avisos…</p>}
        {page && page.items.length === 0 && (
          <p>No hay productos por reponer con los mínimos configurados.</p>
        )}
        <ul className="low-stock-list" inert={disabled || busy}>
          {page?.items.map((item) => (
            <li key={item.id}>
              <ProductThumbnail
                actor={actor}
                productId={item.id}
                name={item.name}
                onProblem={onProblem}
              />
              <div>
                <h3>{item.name}</h3>
                <p>SKU: {item.sku ?? "Sin SKU"}</p>
                <p>
                  Existencias: <strong>{displayDecimal(item.quantity)}</strong>{" "}
                  · Mínimo: <strong>{displayDecimal(item.minimum)}</strong>
                </p>
              </div>
              <button
                className="action"
                aria-label={`Abrir inventario de ${item.name}`}
                disabled={disabled || busy}
                onClick={() => onSelect(item.id)}
              >
                Abrir producto
              </button>
            </li>
          ))}
        </ul>
        <div className="catalog-pagination">
          <button
            className="action"
            disabled={disabled || busy}
            onClick={() => void load()}
          >
            Actualizar avisos
          </button>
          <button
            className="action"
            disabled={disabled || busy || !page?.next_after}
            onClick={() => void load(page?.next_after ?? undefined)}
          >
            Más productos por reponer
          </button>
        </div>
        <p className="membership-help">
          Consulta a demanda. Actualiza los avisos después de registrar
          movimientos o cambiar mínimos. No se crean compras automáticamente.
        </p>
      </div>
    </section>
  );
}
