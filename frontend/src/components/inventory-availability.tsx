"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { sameContext } from "@/lib/access/same-context";
import {
  availabilityApi,
  type AvailabilityQuery,
  type AvailableProduct,
} from "@/lib/inventory/availability";
import type { Page } from "@/lib/catalog/api";
import { displayPrice } from "@/lib/catalog/api";
import { displayDecimal } from "@/lib/inventory/api";
import InventoryWithdrawalForm from "@/components/inventory-withdrawal-form";
import ProductSearch from "@/components/product-search";
import ProductThumbnail from "@/components/product-thumbnail";

export default function InventoryAvailability() {
  const [session, setSession] = useState<SessionContext | null>(null);
  const original = useRef<SessionContext | null>(null);
  const [page, setPage] = useState<Page<AvailableProduct> | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  const [paused, setPaused] = useState(true);
  const [search, setSearch] = useState<AvailabilityQuery>({
    q: "",
    category: null,
  });
  const query = useRef<AvailabilityQuery>({ q: "", category: null });
  const controller = useRef<AbortController | null>(null);
  const content = useRef<HTMLDivElement>(null);
  const focused = useRef<HTMLElement | null>(null);
  const block = useCallback((message: string) => {
    controller.current?.abort();
    setPage(null);
    setSession(null);
    setMessage(message);
    setBusy(false);
    setPaused(false);
  }, []);
  const load = useCallback(
    async (next: AvailabilityQuery) => {
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      setBusy(true);
      setPaused(true);
      setMessage("");
      try {
        const actor = await accessApi.session({ signal: current.signal });
        if (current.signal.aborted) return;
        if (original.current && !sameContext(original.current, actor)) {
          block("La sesión cambió. Vuelve al inicio y comprueba el acceso.");
          return;
        }
        if (!["read_only", "admin", "cashier"].includes(actor.role)) {
          block("Tu perfil no permite esta consulta de inventario.");
          return;
        }
        const result = await availabilityApi.products(
          actor.membership_id,
          next,
          { signal: current.signal },
        );
        if (current.signal.aborted) return;
        original.current = actor;
        setSession(actor);
        setPage(result);
        setSearch(next);
        setPaused(document.visibilityState === "hidden");
        if (
          document.visibilityState !== "hidden" &&
          document.activeElement === document.body &&
          focused.current?.isConnected
        )
          focused.current.focus({ preventScroll: true });
      } catch {
        if (!current.signal.aborted)
          block(
            "No tienes permiso vigente para esta consulta o no pudimos comprobarlo. Solicita autorización al administrador o vuelve a comprobar el acceso.",
          );
      } finally {
        if (!current.signal.aborted) setBusy(false);
      }
    },
    [block],
  );
  useEffect(() => {
    void load(query.current);
    const resume = () => {
      if (document.visibilityState === "visible") void load(query.current);
    };
    const visibility = () => {
      if (document.visibilityState === "hidden") {
        controller.current?.abort();
        setPaused(true);
      } else resume();
    };
    const channel =
      typeof BroadcastChannel !== "undefined"
        ? new BroadcastChannel("carrillo_access")
        : null;
    if (channel)
      channel.onmessage = (event) => {
        if (event.data === "changed") resume();
      };
    window.addEventListener("focus", resume);
    window.addEventListener("pageshow", resume);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      controller.current?.abort();
      window.removeEventListener("focus", resume);
      window.removeEventListener("pageshow", resume);
      document.removeEventListener("visibilitychange", visibility);
      channel?.close();
    };
  }, [load]);
  function refresh(event?: FormEvent) {
    event?.preventDefault();
    void load(query.current);
  }
  return (
    <main id="contenido" className="memberships-workspace" tabIndex={-1}>
      <Link className="text-action" href="/">
        ← Volver al inicio
      </Link>
      <p className="eyebrow">CONSULTA AUTORIZADA</p>
      <h1>Consultar existencias</h1>
      {message && (
        <p role="alert" className="access-message">
          {message}
        </p>
      )}
      <div className="form-actions">
        <button
          className="action secondary"
          disabled={busy}
          onClick={() => refresh()}
        >
          Actualizar consulta
        </button>
      </div>
      {(busy || paused) && (
        <p role="status" className="sr-only">
          Comprobando acceso y existencias…
        </p>
      )}
      {session && page && (
        <div
          ref={content}
          aria-hidden={paused || undefined}
          inert={paused || busy}
          onFocusCapture={(event) => {
            focused.current = event.target;
          }}
        >
          <p>{session.tenant_name} · Consulta de productos activos</p>
          <p>
            {session.role === "cashier"
              ? "Puedes registrar salidas manuales autorizadas. Esta acción no registra ventas ni cobros."
              : "Consulta de existencias sin acceso a costos ni cambios de inventario."}
          </p>
          <ProductSearch
            actor={session.membership_id}
            operational
            value={search.q}
            category={search.category}
            disabled={busy || paused}
            onSearch={(q, category) => {
              query.current = { q, category };
              void load(query.current);
            }}
          />
          {!page.items.length && (
            <p>No hay productos activos con esos filtros.</p>
          )}
          <ul className="availability-list">
            {page.items.map((product) => (
              <li key={product.id}>
                <ProductThumbnail
                  actor={session.membership_id}
                  productId={product.id}
                  name={product.name}
                  availability
                  onProblem={block}
                />
                <div>
                  <h2>{product.name}</h2>
                  <p>SKU: {product.sku ?? "Sin SKU"}</p>
                  <p>Categoría: {product.category ?? "Sin categoría"}</p>
                  <dl>
                    <div>
                      <dt>Existencias disponibles</dt>
                      <dd>{displayDecimal(product.quantity)}</dd>
                    </div>
                    <div>
                      <dt>Precio de venta</dt>
                      <dd>{displayPrice(product.price)}</dd>
                    </div>
                  </dl>
                  {session.role === "cashier" && (
                    <InventoryWithdrawalForm
                      actor={session.membership_id}
                      product={product}
                      onProblem={block}
                      onSaved={() => void load(query.current)}
                    />
                  )}
                </div>
              </li>
            ))}
          </ul>
          <div className="form-actions">
            <button
              className="action secondary"
              disabled={busy || paused}
              onClick={() => {
                query.current = { ...query.current, after: undefined };
                void load(query.current);
              }}
            >
              Inicio de productos
            </button>
            <button
              className="action secondary"
              disabled={busy || paused || !page.next_after}
              onClick={() => {
                query.current = {
                  ...query.current,
                  after: page.next_after ?? undefined,
                };
                void load(query.current);
              }}
            >
              Más productos
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
