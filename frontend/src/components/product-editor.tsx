"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import IdentityConfirmation from "@/components/identity-confirmation";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { sameContext } from "@/lib/access/same-context";
import { ApiError } from "@/lib/api/client";
import {
  catalogApi,
  displayPrice,
  priceInput,
  type Category,
  type ProductChange,
  type VersionedProduct,
} from "@/lib/catalog/api";

type Props = {
  target: string;
  session: SessionContext;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
  onProblem: (message: string) => void;
  onClose: () => void;
  onSaved: () => void;
};
export default function ProductEditor({
  target,
  session,
  onWriting,
  onChanged,
  onProblem,
  onClose,
  onSaved,
}: Props) {
  const formId = useId();
  const [version, setVersion] = useState<VersionedProduct | null>(null);
  const [categories, setCategories] = useState<Category[]>([]);
  const [draft, setDraft] = useState<ProductChange | null>(null);
  const [stage, setStage] = useState<
    "loading" | "identity" | "edit" | "review" | "conflict"
  >("loading");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);
  const deadline = useRef(0);
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const actor = session.membership_id;
  const identityWriting = useCallback(
    (value: boolean) => {
      setBusy(value);
      onWriting(value);
    },
    [onWriting],
  );
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    const timer = setTimeout(() => {
      current.abort();
      onProblem(
        "La consulta del producto tardó demasiado. Comprueba el acceso.",
      );
    }, 30_000);
    async function load() {
      try {
        const value = await catalogApi.editable(actor, target, {
          signal: current.signal,
        });
        const options: Category[] = [];
        let after: string | undefined;
        do {
          const page = await catalogApi.categories(actor, after, {
            signal: current.signal,
          });
          options.push(...page.items);
          if (page.next_after && after && page.next_after <= after)
            throw new Error("Invalid category order");
          after = page.next_after ?? undefined;
        } while (after && !current.signal.aborted);
        if (current.signal.aborted) return;
        if (
          value.product.category_id !== null &&
          !options.some((row) => row.id === value.product.category_id)
        )
          throw new Error("Missing category");
        setVersion(value);
        setDraft({
          name: value.product.name,
          price: value.product.price,
          sku: value.product.sku,
          category_id: value.product.category_id,
          is_active: value.product.is_active,
        });
        setCategories(options);
        setMessage("");
        setStage("identity");
      } catch {
        if (!current.signal.aborted)
          onProblem("No pudimos cargar el producto. Comprueba el acceso.");
      } finally {
        clearTimeout(timer);
      }
    }
    void load();
    return () => {
      current.abort();
      controller.current?.abort();
      clearTimeout(timer);
      onWriting(false);
    };
  }, [actor, target, reload, onProblem, onWriting]);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
    else if (stage !== "identity" && stage !== "loading")
      title.current?.focus({ preventScroll: true });
  }, [message, stage]);
  function authorized() {
    if (performance.now() < deadline.current) return true;
    setMessage("Confirma tu identidad para continuar con estos cambios.");
    setStage("identity");
    return false;
  }
  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current || !draft || !authorized()) return;
    try {
      const name = draft.name.trim(),
        sku = draft.sku?.trim() || null;
      if (
        !name ||
        name.includes("\0") ||
        Array.from(name).length > 200 ||
        !name.isWellFormed() ||
        (sku !== null &&
          (Array.from(sku).length > 100 ||
            sku.includes("\0") ||
            !sku.isWellFormed()))
      )
        throw new Error("Invalid fields");
      setDraft({ ...draft, name, sku, price: priceInput(draft.price) });
      setMessage("");
      setStage("review");
    } catch {
      setMessage("Revisa el nombre, SKU y precio de venta antes de continuar.");
    }
  }
  async function save() {
    if (operation.current || !draft || !version || !authorized()) return;
    operation.current = true;
    setBusy(true);
    onWriting(true);
    const current = new AbortController();
    controller.current = current;
    try {
      await catalogApi.updateProduct(actor, target, version.etag, draft, {
        signal: current.signal,
      });
      const context = await accessApi.session({ signal: current.signal });
      if (!sameContext(context, session) || context.role !== "admin")
        throw new Error("Changed context");
      if (!current.signal.aborted) onSaved();
    } catch (error) {
      if (current.signal.aborted) return;
      if (error instanceof ApiError && error.status === 412) {
        setMessage(
          "Otro administrador cambió este producto. Recárgalo y revisa los datos antes de volver a editar.",
        );
        setStage("conflict");
      } else if (
        error instanceof ApiError &&
        (error.status === 409 || error.status === 422)
      ) {
        setMessage(
          error.status === 409
            ? "Ese SKU ya está en uso. Elige otro."
            : "Revisa los datos del producto.",
        );
        setStage("edit");
      } else
        onProblem(
          "No pudimos confirmar si el producto cambió. Comprueba el acceso y revisa el catálogo antes de intentarlo de nuevo.",
        );
    } finally {
      operation.current = false;
      onWriting(false);
      if (!current.signal.aborted) setBusy(false);
    }
  }
  if (stage === "loading")
    return (
      <section aria-label="Editar producto">
        <p role="status">Cargando producto…</p>
      </section>
    );
  return (
    <section
      className="membership-review"
      aria-label="Editar producto"
      aria-busy={busy}
    >
      <h2 ref={title} tabIndex={-1}>
        {stage === "review"
          ? "Revisa los cambios del producto"
          : "Editar producto"}
      </h2>
      {message && (
        <p className="access-message" ref={notice} role="alert" tabIndex={-1}>
          {message}
        </p>
      )}
      {stage === "identity" && (
        <IdentityConfirmation
          session={session}
          purpose="editar los datos y el estado del producto"
          onWriting={identityWriting}
          onChanged={onChanged}
          onProblem={onProblem}
          onConfirmed={(remaining) => {
            deadline.current = performance.now() + remaining;
            setStage("edit");
            setMessage("");
          }}
        />
      )}
      {stage === "edit" && draft && (
        <form id={formId} className="catalog-form" onSubmit={review}>
          <label>
            Nombre del producto
            <input
              required
              maxLength={200}
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
          </label>
          <label>
            SKU (opcional)
            <input
              maxLength={100}
              value={draft.sku ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, sku: e.target.value || null })
              }
            />
          </label>
          <label>
            Precio de venta (USD)
            <input
              required
              inputMode="decimal"
              maxLength={21}
              value={draft.price}
              onChange={(e) => setDraft({ ...draft, price: e.target.value })}
            />
          </label>
          <label>
            Categoría
            <select
              value={draft.category_id ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, category_id: e.target.value || null })
              }
            >
              <option value="">Sin categoría</option>
              {categories
                .filter(
                  (row) =>
                    row.is_active || row.id === version?.product.category_id,
                )
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                    {row.is_active ? "" : " (inactiva)"}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Estado del producto
            <select
              value={draft.is_active ? "active" : "inactive"}
              onChange={(e) =>
                setDraft({ ...draft, is_active: e.target.value === "active" })
              }
            >
              <option value="active">Activo</option>
              <option value="inactive">Inactivo</option>
            </select>
          </label>
          <p>
            Desactivar conserva el producto, sus fotos, existencias e historial.
            Mientras esté inactivo no podrás registrar nuevos movimientos de
            inventario. El precio de venta no cambia el costo ni las
            existencias.
          </p>
        </form>
      )}
      {stage === "review" && draft && version && (
        <>
          <dl className="movement-summary">
            <div>
              <dt>Nombre</dt>
              <dd>{draft.name}</dd>
            </div>
            <div>
              <dt>SKU</dt>
              <dd>{draft.sku ?? "Sin SKU"}</dd>
            </div>
            <div>
              <dt>Precio de venta</dt>
              <dd>
                {displayPrice(version.product.price)} →{" "}
                {displayPrice(draft.price)}
              </dd>
            </div>
            <div>
              <dt>Categoría</dt>
              <dd>
                {categories.find((row) => row.id === draft.category_id)?.name ??
                  "Sin categoría"}
              </dd>
            </div>
            <div>
              <dt>Estado</dt>
              <dd>
                {version.product.is_active ? "Activo" : "Inactivo"} →{" "}
                {draft.is_active ? "Activo" : "Inactivo"}
              </dd>
            </div>
          </dl>
          <p>Estos cambios no modifican las existencias ni su costo.</p>
        </>
      )}
      <div className="form-actions">
        {stage === "edit" && (
          <button
            className="action"
            type="submit"
            form={formId}
            disabled={busy}
          >
            Revisar cambios
          </button>
        )}
        {stage === "review" && (
          <>
            <button
              className="action"
              disabled={busy}
              onClick={() => void save()}
            >
              {busy ? "Guardando…" : "Guardar cambios"}
            </button>
            <button
              className="action secondary"
              disabled={busy}
              onClick={() => setStage("edit")}
            >
              Corregir datos
            </button>
          </>
        )}
        {stage === "conflict" && (
          <button
            className="action"
            onClick={() => {
              setStage("loading");
              setReload((value) => value + 1);
            }}
          >
            Recargar producto y descartar cambios
          </button>
        )}
        <button className="action secondary" disabled={busy} onClick={onClose}>
          Cancelar, no guardar
        </button>
      </div>
    </section>
  );
}
