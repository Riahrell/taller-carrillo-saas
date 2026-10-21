"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import IdentityConfirmation from "@/components/identity-confirmation";
import ProductSearch from "@/components/product-search";
import ProductImage, {
  type ScopedImagePending,
} from "@/components/product-image";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import {
  catalogApi,
  displayPrice,
  type Page,
  type Product,
} from "@/lib/catalog/api";
import {
  inventoryApi,
  quantityInput,
  costInput,
  reasonInput,
  displayDecimal,
  type Balance,
  type CountInput,
  type PendingCount,
  type ManualInput,
  type HistoryEntry,
} from "@/lib/inventory/api";

export type ScopedPending = { session: SessionContext; command: PendingCount };
export const uncertainInventory =
  "La actualización puede haberse guardado. Comprueba tu acceso y revisa la actualización pendiente antes de volver a cambiar las existencias.";
export function sameInventorySession(a: SessionContext, b: SessionContext) {
  return (
    a.user_id === b.user_id &&
    a.membership_id === b.membership_id &&
    a.tenant_id === b.tenant_id &&
    a.role === "admin" &&
    b.role === "admin"
  );
}
type Selection = {
  product: Product;
  balance: Balance;
  history: Page<HistoryEntry>;
  category: string;
};
async function selection(
  actor: string,
  target: string,
  signal: AbortSignal,
): Promise<Selection> {
  const [product, balance, history] = await Promise.all([
    catalogApi.detail(actor, target, { signal }),
    inventoryApi.balance(actor, target, { signal }),
    inventoryApi.history(actor, target, undefined, { signal }),
  ]);
  let category = "Sin categoría";
  if (product.category_id !== null) {
    let after: string | undefined;
    // Traverse bounded, strictly advancing pages; cancellation clears the view.
    // A missing category is an inconsistent read, never an invented name.
    do {
      const page = await catalogApi.categories(actor, after, { signal });
      const found = page.items.find((row) => row.id === product.category_id);
      if (found) {
        category = found.name;
        break;
      }
      if (!page.next_after) throw new Error("Missing category");
      after = page.next_after;
    } while (!signal.aborted);
  }
  return { product, balance, history, category };
}
type Props = {
  initialImagePending: ScopedImagePending | null;
  onImagePending: (value: ScopedImagePending | null) => void;
  session: SessionContext;
  initialPending: ScopedPending | null;
  onPending: (value: ScopedPending | null) => void;
  onProblem: (message: string) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
};
type Stage = "closed" | "identity" | "edit" | "review" | "recover";
const date = new Intl.DateTimeFormat("es-SV", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/El_Salvador",
});
export default function InventoryManager({
  initialImagePending,
  onImagePending,
  session,
  initialPending,
  onPending,
  onProblem,
  onWriting,
  onChanged,
}: Props) {
  const actor = session.membership_id;
  const movementFormId = useId();
  const [imageEngaged, setImageEngaged] = useState(
    initialImagePending !== null,
  );
  const [imagePending, setImagePending] = useState(initialImagePending);
  const rememberImage = useCallback(
    (value: ScopedImagePending | null) => {
      setImagePending(value);
      onImagePending(value);
    },
    [onImagePending],
  );
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
  const [products, setProducts] = useState<Page<Product> | null>(null);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [pending, setPending] = useState<PendingCount | null>(
    initialPending?.command ?? null,
  );
  const [stage, setStage] = useState<Stage>("closed");
  const [quantity, setQuantity] = useState("");
  const [reason, setReason] = useState("");
  const [cost, setCost] = useState("");
  const [mode, setMode] = useState<"COUNT" | "IN" | "OUT">(
    initialPending && "direction" in initialPending.command.data
      ? initialPending.command.data.direction
      : "COUNT",
  );
  const actionLabel =
    mode === "IN"
      ? "Registrar entrada"
      : mode === "OUT"
        ? "Registrar salida"
        : "Actualizar existencias";
  const [review, setReview] = useState<CountInput | ManualInput | null>(null);
  const [deadline, setDeadline] = useState<number | null>(null);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const [success, setSuccess] = useState(false);
  const alive = useRef(true);
  const locked = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const reportWriting = useCallback(
    (value: boolean) => {
      if (alive.current) onWriting(value);
    },
    [onWriting],
  );
  const identityWriting = useCallback(
    (value: boolean) => {
      if (alive.current) {
        setBusy(value);
        reportWriting(value);
      }
    },
    [reportWriting],
  );
  const remember = (value: PendingCount | null) => {
    setPending(value);
    onPending(value ? { session, command: value } : null);
  };
  function clearDraft() {
    setQuantity("");
    setReason("");
    setCost("");
    setReview(null);
  }
  useEffect(() => {
    alive.current = true;
    const current = new AbortController();
    controller.current = current;
    const timer = setTimeout(() => {
      if (!current.signal.aborted) {
        current.abort();
        onProblem(
          "La consulta de inventario tardó demasiado. Comprueba el acceso.",
        );
      }
    }, 30_000);
    void catalogApi
      .products(actor, undefined, { signal: current.signal })
      .then(async (page) => {
        const requested = new URL(window.location.href).searchParams.getAll(
          "producto",
        );
        const requestedId =
          requested.length === 1 &&
          /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
            requested[0],
          )
            ? requested[0]
            : undefined;
        if (requested.length > 0 && requestedId === undefined)
          throw new Error("Invalid product selection");
        const target =
          initialPending?.command.data.product_id ??
          initialImagePending?.command.product_id ??
          requestedId ??
          page.items[0]?.id;
        const detail = target
          ? await selection(actor, target, current.signal)
          : null;
        if (!current.signal.aborted) {
          setProducts(page);
          setSelected(detail);
          setBusy(false);
          locked.current = false;
        }
      })
      .catch(() => {
        if (!current.signal.aborted)
          onProblem("No pudimos consultar el inventario. Comprueba el acceso.");
      })
      .finally(() => clearTimeout(timer));
    return () => {
      alive.current = false;
      current.abort();
      controller.current?.abort();
      onWriting(false);
      clearTimeout(timer);
    };
  }, [actor, initialPending, initialImagePending, onProblem, onWriting]);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
    else if (stage !== "identity")
      heading.current?.focus({ preventScroll: true });
  }, [message, stage]);
  useEffect(() => {
    if (deadline === null || stage === "closed" || stage === "identity") return;
    const timer = setTimeout(
      () => {
        if (locked.current) {
          onProblem(uncertainInventory);
          return;
        }
        setQuantity("");
        setReason("");
        setCost("");
        setReview(null);
        setDeadline(null);
        setStage("identity");
        setMessage("La confirmación venció. Confirma tu identidad de nuevo.");
      },
      Math.max(0, deadline - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [deadline, stage, onProblem]);
  async function read(action: (signal: AbortSignal) => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    const timer = setTimeout(() => {
      current.abort();
      if (alive.current)
        onProblem("La consulta tardó demasiado. Comprueba el acceso.");
    }, 30_000);
    try {
      await action(current.signal);
    } catch {
      if (!current.signal.aborted)
        onProblem("No pudimos actualizar el inventario. Comprueba el acceso.");
    } finally {
      clearTimeout(timer);
      if (!current.signal.aborted && alive.current) {
        locked.current = false;
        setBusy(false);
      }
    }
  }
  async function choose(target: string) {
    await read(async (signal) => {
      const detail = await selection(actor, target, signal);
      if (!signal.aborted) setSelected(detail);
    });
  }
  async function productPage(
    after?: string,
    term = search,
    category = categoryFilter,
  ) {
    await read(async (signal) => {
      const page = await catalogApi.products(
        actor,
        after,
        { signal },
        term,
        category,
      );
      const detail = page.items[0]
        ? await selection(actor, page.items[0].id, signal)
        : null;
      if (!signal.aborted) {
        setProducts(page);
        setSearch(term);
        setCategoryFilter(category);
        setSelected(detail);
      }
    });
  }
  async function historyPage(after?: HistoryEntry) {
    if (!selected) return;
    await read(async (signal) => {
      const history = await inventoryApi.history(
        actor,
        selected.product.id,
        after,
        { signal },
      );
      if (!signal.aborted) setSelected({ ...selected, history });
    });
  }
  let needsCost = mode === "IN";
  if (selected && mode === "COUNT") {
    try {
      needsCost =
        BigInt(quantityInput(quantity).replace(".", "")) >
          BigInt(selected.balance.quantity.replace(".", "")) &&
        (BigInt(selected.balance.quantity.replace(".", "")) === 0n ||
          BigInt(selected.balance.value.replace(".", "")) === 0n);
    } catch {
      /* A draft may be incomplete. */
    }
  }
  function prepare(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || busy) return;
    try {
      const amount = quantityInput(quantity);
      if (mode !== "COUNT" && BigInt(amount.replace(".", "")) <= 0n)
        throw new Error("Positive quantity required");
      setReview(
        mode === "COUNT"
          ? {
              product_id: selected.product.id,
              counted_quantity: amount,
              reason: reasonInput(reason),
              declared_unit_cost: needsCost ? costInput(cost) : null,
            }
          : {
              product_id: selected.product.id,
              direction: mode,
              quantity: amount,
              reason: reasonInput(reason),
              unit_cost: mode === "IN" ? costInput(cost) : null,
            },
      );
      setMessage("");
      setStage("review");
    } catch {
      setMessage(
        "Revisa la cantidad (hasta 3 decimales), el motivo y el costo requerido (hasta 6 decimales).",
      );
    }
  }
  async function save() {
    if (locked.current || !selected || (!pending && !review)) return;
    if (deadline === null || performance.now() >= deadline) {
      clearDraft();
      setStage("identity");
      setMessage("Confirma tu identidad de nuevo.");
      return;
    }
    if (
      !pending &&
      (typeof crypto === "undefined" || typeof crypto.randomUUID !== "function")
    ) {
      onProblem(
        "No se pudo preparar la actualización. Comprueba tu acceso desde un navegador actualizado.",
      );
      return;
    }
    const command =
      pending ??
      Object.freeze({
        key: crypto.randomUUID(),
        data: Object.freeze({ ...review! }),
      });
    remember(command);
    locked.current = true;
    reportWriting(true);
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    try {
      await inventoryApi.record(actor, command, { signal: current.signal });
      if (current.signal.aborted) return;
      const context = await accessApi.session({ signal: current.signal });
      if (!sameInventorySession(context, session)) {
        onProblem(uncertainInventory);
        return;
      }
      const detail = await selection(
        actor,
        command.data.product_id,
        current.signal,
      );
      if (current.signal.aborted) return;
      setSelected(detail);
      remember(null);
      clearDraft();
      setStage("closed");
      setSuccess(true);
      setMessage(
        "Existencias actualizadas. Se muestran las existencias y el costo calculados hasta esta consulta.",
      );
    } catch (error) {
      if (current.signal.aborted) return;
      setSuccess(false);
      if (error instanceof ApiError && error.status === 422) {
        remember(null);
        try {
          const detail = await selection(
            actor,
            command.data.product_id,
            current.signal,
          );
          if (current.signal.aborted) return;
          setSelected(detail);
          setStage("edit");
          setMessage(
            "No se guardó la actualización. Revisa las existencias actuales, la cantidad y el costo antes de volver a confirmar.",
          );
        } catch {
          if (!current.signal.aborted)
            onProblem(
              "No se guardó la actualización y no pudimos consultar las existencias actuales. Comprueba el acceso.",
            );
        }
      } else if (error instanceof ApiError && error.status === 428) {
        setStage("identity");
        setDeadline(null);
        setMessage(
          "Comprueba tu acceso para revisar la actualización pendiente.",
        );
      } else if (
        error instanceof ApiError &&
        (error.kind === "network" || error.status === 503)
      ) {
        setStage("closed");
        setMessage(uncertainInventory);
      } else {
        onProblem(uncertainInventory);
      }
    } finally {
      if (!current.signal.aborted && alive.current) {
        locked.current = false;
        reportWriting(false);
        setBusy(false);
      }
    }
  }
  const frozen = busy || stage !== "closed" || pending !== null || imageEngaged;
  const reviewed = stage === "recover" ? pending?.data : review;
  return (
    <section
      className="inventory-manager"
      aria-label="Inventario de la empresa"
      aria-busy={busy}
    >
      <div className="catalog-toolbar">
        <Link className="text-action" href="/catalogo">
          Administrar productos y categorías →
        </Link>
        <button
          className="action"
          disabled={busy || stage !== "closed"}
          onClick={() => {
            if (selected) void choose(selected.product.id);
            else void productPage();
          }}
        >
          Actualizar consulta
        </button>
      </div>
      {message && (
        <p
          ref={notice}
          tabIndex={-1}
          role={success ? "status" : "alert"}
          className="membership-notice"
        >
          {message}
        </p>
      )}
      {pending && stage === "closed" && (
        <section
          className="membership-notice"
          aria-label="Actualización pendiente"
        >
          <h2>Resultado por confirmar</h2>
          <p>
            Revisaremos si esta actualización ya se guardó, sin duplicarla.
            Mantén esta pantalla abierta hasta conocer el resultado.
          </p>
          <p>
            Referencia: <code>{pending.key}</code>
          </p>
          <button
            className="action primary"
            disabled={busy}
            onClick={() => {
              setMessage("");
              setStage("identity");
            }}
          >
            Comprobar actualización pendiente
          </button>
        </section>
      )}
      <div className="inventory-columns">
        <section
          aria-label="Productos de inventario"
          className="inventory-products"
        >
          <h2>Productos</h2>
          <ProductSearch
            actor={actor}
            category={categoryFilter}
            value={search}
            disabled={frozen}
            onSearch={(term, category) =>
              void productPage(undefined, term, category)
            }
          />
          {!products ? (
            <p role="status">Cargando productos…</p>
          ) : (
            <>
              <ul className="inventory-product-list">
                {products.items.map((product) => (
                  <li key={product.id}>
                    <button
                      className="inventory-product"
                      aria-pressed={selected?.product.id === product.id}
                      disabled={frozen}
                      onClick={() => void choose(product.id)}
                    >
                      <strong>{product.name}</strong>
                      <span>SKU: {product.sku ?? "Sin SKU"}</span>
                      <span>{product.is_active ? "Activo" : "Inactivo"}</span>
                    </button>
                  </li>
                ))}
              </ul>
              {products.items.length === 0 && (
                <p>
                  {search || categoryFilter
                    ? "No se encontraron productos con esos filtros."
                    : "No hay productos. Créalos en el catálogo para comenzar."}
                </p>
              )}
              <div className="catalog-pagination">
                <button
                  className="action"
                  disabled={frozen}
                  onClick={() => void productPage()}
                >
                  Inicio de productos
                </button>
                <button
                  className="action"
                  disabled={frozen || !products.next_after}
                  onClick={() =>
                    void productPage(products.next_after ?? undefined)
                  }
                >
                  Más productos
                </button>
              </div>
            </>
          )}
        </section>
        {selected && (
          <section
            className="inventory-detail"
            aria-label="Ficha de inventario"
          >
            <div className="inventory-detail-heading">
              <ProductImage
                key={selected.product.id}
                session={session}
                productId={selected.product.id}
                productName={selected.product.name}
                disabled={
                  busy ||
                  stage !== "closed" ||
                  pending !== null ||
                  !selected.product.is_active
                }
                initialPending={
                  imagePending?.command.product_id === selected.product.id
                    ? imagePending
                    : null
                }
                onPending={rememberImage}
                onEngaged={setImageEngaged}
                onWriting={reportWriting}
                onChanged={onChanged}
                onProblem={onProblem}
              />
              <div>
                <p className="eyebrow">
                  PRODUCTO ·{" "}
                  {selected.product.is_active ? "ACTIVO" : "INACTIVO"}
                </p>
                <h2 ref={heading} tabIndex={-1}>
                  {selected.product.name}
                </h2>
                <p>SKU: {selected.product.sku ?? "Sin SKU"}</p>
                <p>Categoría: {selected.category}</p>
              </div>
            </div>
            <dl className="inventory-metrics">
              <div>
                <dt>Existencia actual</dt>
                <dd>{displayDecimal(selected.balance.quantity)}</dd>
              </div>
              <div>
                <dt>Costo promedio / unidad</dt>
                <dd>
                  {selected.balance.average_unit_cost === null
                    ? "Sin costo calculable"
                    : `${displayDecimal(selected.balance.average_unit_cost, 2)} USD`}
                </dd>
              </div>
              <div>
                <dt>Precio de venta</dt>
                <dd>{displayPrice(selected.product.price)}</dd>
              </div>
              <div>
                <dt>Valor de existencias</dt>
                <dd>{displayDecimal(selected.balance.value, 2)} USD</dd>
              </div>
            </dl>
            <p className="membership-help">
              El costo proviene de los movimientos. El precio de venta no
              modifica las existencias ni su valoración.
            </p>
            {stage === "closed" && !pending && (
              <div className="catalog-tabs">
                {(
                  [
                    ["IN", "Registrar entrada"],
                    ["OUT", "Registrar salida"],
                    ["COUNT", "Actualizar existencias"],
                  ] as const
                ).map(([kind, label]) => (
                  <button
                    key={kind}
                    className="action primary"
                    disabled={
                      busy || imageEngaged || !selected.product.is_active
                    }
                    onClick={() => {
                      clearDraft();
                      setMode(kind);
                      setSuccess(false);
                      setMessage("");
                      setStage("identity");
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
            {stage === "identity" && (
              <IdentityConfirmation
                purpose={
                  pending
                    ? "comprobar la actualización pendiente"
                    : actionLabel.toLowerCase()
                }
                session={session}
                onWriting={identityWriting}
                onChanged={onChanged}
                onProblem={onProblem}
                onConfirmed={(remaining) => {
                  setDeadline(performance.now() + remaining);
                  setStage(pending ? "recover" : "edit");
                  setMessage("");
                }}
              />
            )}
            {stage === "edit" && (
              <form
                id={movementFormId}
                onSubmit={prepare}
                className="catalog-form"
              >
                <h3>{actionLabel}</h3>
                <label>
                  {mode === "COUNT"
                    ? "Existencias reales en bodega"
                    : mode === "IN"
                      ? "Unidades que entran"
                      : "Unidades que salen"}
                  <input
                    inputMode="decimal"
                    autoComplete="off"
                    required
                    maxLength={19}
                    value={quantity}
                    onChange={(event) => setQuantity(event.target.value)}
                  />
                </label>
                <p>
                  {mode === "COUNT"
                    ? "Escribe cuántas unidades hay ahora. Escribe 0 si no hay."
                    : "Escribe cuántas unidades se agregan o retiran, no las existencias finales. La cantidad debe ser mayor que cero."}{" "}
                  Puedes usar hasta tres decimales.
                </p>
                <label>
                  ¿Por qué cambian las existencias?
                  <textarea
                    required
                    maxLength={500}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </label>
                {needsCost && (
                  <label>
                    Costo por unidad (USD)
                    <input
                      inputMode="decimal"
                      autoComplete="off"
                      required
                      maxLength={29}
                      value={cost}
                      onChange={(event) => setCost(event.target.value)}
                    />
                    <small>
                      {mode === "IN"
                        ? "Indica cuánto costó cada unidad que entra. No es el precio de venta."
                        : "Indica cuánto costó cada unidad. Hace falta este dato porque el inventario aún no tiene un costo calculable."}{" "}
                      Puedes usar hasta seis decimales.
                    </small>
                  </label>
                )}
                <p>
                  En el siguiente paso revisarás los datos antes de guardar.
                </p>
              </form>
            )}
            {(stage === "review" || stage === "recover") && reviewed && (
              <section
                className="catalog-review"
                aria-label="Revisión de la actualización"
              >
                <h3>
                  {pending
                    ? "Comprobar actualización pendiente"
                    : mode === "COUNT"
                      ? "Actualizar inventario"
                      : actionLabel}
                </h3>
                <dl className="movement-summary">
                  <div>
                    <dt>Producto</dt>
                    <dd>{selected.product.name}</dd>
                  </div>
                  <div>
                    <dt>
                      {"direction" in reviewed
                        ? reviewed.direction === "IN"
                          ? "Unidades que entran"
                          : "Unidades que salen"
                        : "Existencias reales en bodega"}
                    </dt>
                    <dd>
                      {displayDecimal(
                        "direction" in reviewed
                          ? reviewed.quantity
                          : reviewed.counted_quantity,
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>¿Por qué cambian las existencias?</dt>
                    <dd>{reviewed.reason}</dd>
                  </div>
                  <div>
                    <dt>Costo por unidad</dt>
                    <dd>
                      {("direction" in reviewed
                        ? reviewed.unit_cost
                        : reviewed.declared_unit_cost) === null
                        ? "Usar el costo del inventario actual, si corresponde"
                        : `${displayDecimal(("direction" in reviewed ? reviewed.unit_cost : reviewed.declared_unit_cost)!, 2)} USD`}
                    </dd>
                  </div>
                </dl>
                <p>
                  {"direction" in reviewed
                    ? "Al confirmar, se sumarán o restarán las unidades al saldo actual del servidor. Si no hay suficientes unidades, la salida se rechazará sin cambiar el inventario."
                    : "Al confirmar, se ajustarán las existencias a la cantidad que indicaste y se calculará el cambio de valor."}{" "}
                  Esta acción no registra una compra, una venta ni un pago.
                </p>
              </section>
            )}
            {stage !== "closed" && (
              <div className="form-actions">
                {stage === "edit" && (
                  <>
                    <button
                      className="action primary"
                      type="submit"
                      form={movementFormId}
                      disabled={busy}
                    >
                      {mode === "COUNT"
                        ? "Actualizar inventario"
                        : mode === "IN"
                          ? "Revisar entrada"
                          : "Revisar salida"}
                    </button>
                  </>
                )}
                {(stage === "review" || stage === "recover") && reviewed && (
                  <>
                    <button
                      className="action primary"
                      disabled={busy}
                      onClick={() => void save()}
                    >
                      {busy
                        ? "Confirmando…"
                        : pending
                          ? "Comprobar actualización pendiente"
                          : mode === "COUNT"
                            ? "Confirmar actualización"
                            : mode === "IN"
                              ? "Confirmar entrada"
                              : "Confirmar salida"}
                    </button>
                    {!pending && (
                      <button
                        className="action secondary"
                        disabled={busy}
                        onClick={() => setStage("edit")}
                      >
                        Corregir datos
                      </button>
                    )}
                  </>
                )}
                <button
                  className="action secondary"
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    clearDraft();
                    setStage("closed");
                  }}
                >
                  {pending ? "Volver al producto" : "Cancelar, no cambiar nada"}
                </button>
              </div>
            )}
            <section
              aria-label="Historial de inventario"
              className="inventory-history"
            >
              <h3>Historial de inventario</h3>
              <p>
                Hora de El Salvador. Entradas, salidas, ajustes y recepciones de
                compra, del más reciente al más antiguo. Incluye ajustes sin
                cambio de existencias.
              </p>
              {selected.history.items.length === 0 ? (
                <p>Todavía no hay movimientos registrados.</p>
              ) : (
                <ol>
                  {selected.history.items.map((item) => (
                    <li key={`${item.source}:${item.id}`}>
                      <strong>
                        {item.source === "COUNT"
                          ? "Ajuste de existencias"
                          : item.source === "RECEIPT"
                            ? "Recepción de compra"
                            : item.direction === "IN"
                              ? "Entrada manual"
                              : "Salida manual"}{" "}
                        · {date.format(new Date(item.created_at))}
                      </strong>
                      <p>Registrado por: {item.recorded_by}</p>
                      {item.reason && <p>{item.reason}</p>}
                      <details>
                        <summary>Referencia del origen</summary>
                        <p>
                          {item.source === "RECEIPT" ? "Compra" : "Registro"}:{" "}
                          {item.source_document_id}
                        </p>
                      </details>
                      <dl>
                        <div>
                          <dt>Existencias anteriores → actuales</dt>
                          <dd>
                            {displayDecimal(item.before_quantity)} →{" "}
                            {displayDecimal(item.after_quantity)}
                          </dd>
                        </div>
                        <div>
                          <dt>Cambio en existencias</dt>
                          <dd>{displayDecimal(item.quantity_delta)}</dd>
                        </div>
                        <div>
                          <dt>Cambio en el valor del inventario</dt>
                          <dd>{displayDecimal(item.value_delta, 2)} USD</dd>
                        </div>
                      </dl>
                    </li>
                  ))}
                </ol>
              )}
              <div className="catalog-pagination">
                <button
                  className="action"
                  disabled={frozen}
                  onClick={() => void historyPage()}
                >
                  Movimientos recientes
                </button>
                <button
                  className="action"
                  disabled={frozen || !selected.history.next_after}
                  onClick={() =>
                    void historyPage(selected.history.items.at(-1))
                  }
                >
                  Movimientos anteriores
                </button>
              </div>
            </section>
          </section>
        )}
      </div>
      <p className="membership-help">
        Consulta privada. Crear un producto no lo publica en la vitrina. Si
        cierras esta pantalla con una actualización sin confirmar, revisa su
        historial antes de volver a cambiar las existencias.
      </p>
    </section>
  );
}
