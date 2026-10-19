"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import IdentityConfirmation from "@/components/identity-confirmation";
import ProductThumbnail from "@/components/product-thumbnail";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import {
  catalogApi,
  displayPrice,
  priceInput,
  type Category,
  type Product,
  type Page,
} from "@/lib/catalog/api";

type Kind = "products" | "categories";
function isProduct(item: Category): item is Product {
  return "price" in item;
}
export const uncertainCatalog =
  "No pudimos confirmar el resultado de la operación. Comprueba el acceso y revisa el catálogo antes de repetirla.";
type Props = {
  session: SessionContext;
  onProblem: (message: string) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
};
export default function CatalogManager({
  session,
  onProblem,
  onWriting,
  onChanged,
}: Props) {
  const [kind, setKind] = useState<Kind>("products");
  const [products, setProducts] = useState<Page<Product> | null>(null);
  const [categories, setCategories] = useState<Page<Category> | null>(null);
  const [stage, setStage] = useState<"closed" | "identity" | "edit" | "review">(
    "closed",
  );
  const [name, setName] = useState("");
  const [price, setPrice] = useState("");
  const [sku, setSku] = useState("");
  const [selected, setSelected] = useState<Category | null>(null);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const [success, setSuccess] = useState(false);
  const [deadline, setDeadline] = useState<number | null>(null);
  const operation = useRef(true);
  const saving = useRef(false);
  const alive = useRef(true);
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
  const identity = session.membership_id;
  useEffect(() => {
    alive.current = true;
    const current = new AbortController();
    controller.current = current;
    void Promise.all([
      catalogApi.products(identity, undefined, { signal: current.signal }),
      catalogApi.categories(identity, undefined, { signal: current.signal }),
    ])
      .then(([p, c]) => {
        if (!current.signal.aborted) {
          setProducts(p);
          setCategories(c);
          setBusy(false);
          operation.current = false;
        }
      })
      .catch(() => {
        if (!current.signal.aborted)
          onProblem(
            "No pudimos consultar el catálogo. Comprueba el acceso para continuar.",
          );
      });
    return () => {
      alive.current = false;
      controller.current?.abort();
      onWriting(false);
    };
  }, [identity, onProblem, onWriting]);
  useEffect(() => {
    if (deadline === null) return;
    const timer = setTimeout(
      () => {
        if (stage === "closed") {
          setDeadline(null);
          return;
        }
        onProblem(
          saving.current
            ? uncertainCatalog
            : "La confirmación de identidad venció. Comprueba el acceso y revisa el alta de nuevo.",
        );
      },
      Math.max(0, deadline - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [deadline, stage, onProblem]);
  useEffect(() => {
    if (message) notice.current?.focus();
    else if (stage !== "identity") heading.current?.focus();
  }, [message, stage]);
  function reset() {
    setName("");
    setPrice("");
    setSku("");
    setSelected(null);
    setStage("closed");
  }
  async function page(collection: Kind, after?: string) {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    if (collection === "products") setProducts(null);
    else setCategories(null);
    try {
      if (collection === "products") {
        const value = await catalogApi.products(identity, after, {
          signal: current.signal,
        });
        if (!current.signal.aborted) setProducts(value);
      } else {
        const value = await catalogApi.categories(identity, after, {
          signal: current.signal,
        });
        if (!current.signal.aborted) setCategories(value);
      }
    } catch {
      if (!current.signal.aborted)
        onProblem(
          "No pudimos consultar el catálogo. Comprueba el acceso para continuar.",
        );
    } finally {
      operation.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || operation.current) return;
    setSuccess(false);
    try {
      if (
        !name.trim() ||
        Array.from(name.trim()).length > 200 ||
        Array.from(sku.trim()).length > 100
      )
        throw new Error("Invalid name");
      if (kind === "products") setPrice(priceInput(price));
      setName(name.trim());
      setSku(sku.trim());
      setMessage("");
      setStage("review");
    } catch {
      setMessage(
        "Revisa el nombre, el SKU y el precio. Usa hasta dos decimales, sin separadores de miles.",
      );
    }
  }
  async function save() {
    if (operation.current || stage !== "review") return;
    operation.current = true;
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    reportWriting(true);
    saving.current = true;
    try {
      const created =
        kind === "products"
          ? await catalogApi.createProduct(
              identity,
              {
                name,
                price: priceInput(price),
                sku: sku || null,
                category_id: selected?.id ?? null,
              },
              { signal: current.signal },
            )
          : await catalogApi.createCategory(identity, name, {
              signal: current.signal,
            });
      if (current.signal.aborted) return;
      const context = await accessApi.session({ signal: current.signal });
      if (
        context.user_id !== session.user_id ||
        context.tenant_id !== session.tenant_id ||
        context.membership_id !== identity ||
        context.role !== "admin" ||
        created.name !== name ||
        !created.is_active
      )
        throw new Error("Changed context or result");
      if (kind === "products") {
        const checked = await catalogApi.detail(identity, created.id, {
          signal: current.signal,
        });
        if (
          checked.name !== name ||
          checked.price !== priceInput(price) ||
          checked.sku !== (sku || null) ||
          checked.category_id !== (selected?.id ?? null) ||
          !checked.is_active
        )
          throw new Error("Changed product");
      }
      const [p, c] = await Promise.all([
        catalogApi.products(identity, undefined, { signal: current.signal }),
        catalogApi.categories(identity, undefined, { signal: current.signal }),
      ]);
      if (!current.signal.aborted) {
        setProducts(p);
        setCategories(c);
        reset();
        setSuccess(true);
        setMessage(
          `${kind === "products" ? "Producto creado" : "Categoría creada"}: ${created.name}.`,
        );
      }
    } catch (error) {
      if (current.signal.aborted) return;
      if (
        error instanceof ApiError &&
        (error.status === 409 || error.status === 422)
      ) {
        setSuccess(false);
        setStage("edit");
        setMessage(
          error.status === 409
            ? "Ese SKU ya está en uso. Revisa el catálogo antes de repetir el alta."
            : "Revisa los datos y vuelve a revisar el alta.",
        );
      } else
        onProblem(
          error instanceof ApiError && error.status === 428
            ? "La confirmación de identidad venció. Comprueba el acceso y revisa el alta de nuevo."
            : uncertainCatalog,
        );
    } finally {
      operation.current = false;
      saving.current = false;
      if (!current.signal.aborted) {
        reportWriting(false);
        setBusy(false);
      }
    }
  }
  const currentPage = kind === "products" ? products : categories;
  const options = (categories?.items ?? []).filter((item) => item.is_active);
  if (selected && !options.some((item) => item.id === selected.id))
    options.unshift(selected);
  return (
    <section
      aria-label="Administración del catálogo"
      aria-busy={busy}
      className="catalog-manager"
    >
      <div className="catalog-toolbar">
        <div className="catalog-tabs" aria-label="Tipo de catálogo">
          <button
            className="action"
            aria-pressed={kind === "products"}
            disabled={busy || stage !== "closed"}
            onClick={() => {
              setKind("products");
              setMessage("");
            }}
          >
            Productos
          </button>
          <button
            className="action"
            aria-pressed={kind === "categories"}
            disabled={busy || stage !== "closed"}
            onClick={() => {
              setKind("categories");
              setMessage("");
            }}
          >
            Categorías
          </button>
        </div>
        <button
          className="action primary"
          disabled={busy || stage !== "closed"}
          onClick={() => {
            reset();
            setMessage("");
            setSuccess(false);
            if (deadline !== null && performance.now() < deadline)
              setStage("edit");
            else {
              setDeadline(null);
              setStage("identity");
            }
          }}
        >
          {kind === "products" ? "Nuevo producto" : "Nueva categoría"}
        </button>
      </div>
      {message && (
        <p
          role={success ? "status" : "alert"}
          ref={notice}
          tabIndex={-1}
          className="access-message"
        >
          {message}
        </p>
      )}
      {busy && (
        <p role="status">
          {stage === "review" ? "Confirmando el alta…" : "Consultando…"}
        </p>
      )}
      {stage === "identity" && (
        <section aria-label="Autorizar alta">
          <IdentityConfirmation
            session={session}
            purpose="crear productos o categorías"
            onWriting={identityWriting}
            onChanged={onChanged}
            onProblem={onProblem}
            onConfirmed={(remaining) => {
              setDeadline(performance.now() + remaining);
              setStage("edit");
            }}
          />
          <button className="text-action" disabled={busy} onClick={reset}>
            Cancelar alta
          </button>
        </section>
      )}
      {(stage === "edit" || stage === "review") && (
        <section className="membership-review membership-creation">
          <h2 ref={heading} tabIndex={-1}>
            {stage === "review"
              ? "Revisa el alta"
              : kind === "products"
                ? "Datos del producto"
                : "Datos de la categoría"}
          </h2>
          {stage === "edit" ? (
            <form method="post" action="/catalogo" onSubmit={review}>
              <fieldset className="membership-fields" disabled={busy}>
                <legend>
                  {kind === "products" ? "Producto nuevo" : "Categoría nueva"}
                </legend>
                <label htmlFor="catalog-name">Nombre</label>
                <input
                  id="catalog-name"
                  name="name"
                  required
                  maxLength={200}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
                {kind === "products" && (
                  <>
                    <label htmlFor="catalog-price">Precio de venta (USD)</label>
                    <input
                      id="catalog-price"
                      name="price"
                      required
                      inputMode="decimal"
                      value={price}
                      onChange={(e) => setPrice(e.target.value)}
                      aria-describedby="catalog-price-help"
                    />
                    <p id="catalog-price-help" className="field-help">
                      Hasta dos decimales; sin separadores de miles. El precio
                      debe indicarse, incluso si es cero.
                    </p>
                    <label htmlFor="catalog-sku">SKU (opcional)</label>
                    <input
                      id="catalog-sku"
                      name="sku"
                      maxLength={100}
                      value={sku}
                      onChange={(e) => setSku(e.target.value)}
                    />
                    <label htmlFor="catalog-category">
                      Categoría (opcional)
                    </label>
                    <select
                      id="catalog-category"
                      value={selected?.id ?? ""}
                      onChange={(e) =>
                        setSelected(
                          options.find((item) => item.id === e.target.value) ??
                            null,
                        )
                      }
                    >
                      <option value="">Sin categoría</option>
                      {options.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                    <div className="catalog-tabs">
                      <button
                        type="button"
                        className="text-action"
                        disabled={busy}
                        onClick={() => void page("categories")}
                      >
                        Primeras categorías
                      </button>
                      {categories?.next_after && (
                        <button
                          type="button"
                          className="text-action"
                          disabled={busy}
                          onClick={() =>
                            void page("categories", categories.next_after!)
                          }
                        >
                          Más categorías
                        </button>
                      )}
                    </div>
                    <p className="field-help">
                      Crear el producto no registra existencias.
                    </p>
                  </>
                )}
                <button className="action primary" type="submit">
                  Revisar alta
                </button>
              </fieldset>
            </form>
          ) : (
            <>
              <dl className="change-summary">
                <div>
                  <dt>Nombre</dt>
                  <dd>{name}</dd>
                </div>
                {kind === "products" && (
                  <>
                    <div>
                      <dt>Precio de venta</dt>
                      <dd>{displayPrice(price)}</dd>
                    </div>
                    <div>
                      <dt>SKU</dt>
                      <dd>{sku || "Sin SKU"}</dd>
                    </div>
                    <div>
                      <dt>Categoría</dt>
                      <dd>{selected?.name ?? "Sin categoría"}</dd>
                    </div>
                  </>
                )}
              </dl>
              <div className="catalog-tabs">
                <button
                  className="action primary"
                  disabled={busy}
                  onClick={() => void save()}
                >
                  Confirmar alta
                </button>
                <button
                  className="text-action"
                  disabled={busy}
                  onClick={() => setStage("edit")}
                >
                  Volver a editar
                </button>
              </div>
            </>
          )}
          <button className="text-action" disabled={busy} onClick={reset}>
            Cancelar alta
          </button>
        </section>
      )}
      {stage === "closed" && (
        <>
          <h2 ref={heading} tabIndex={-1}>
            {kind === "products"
              ? "Productos de esta empresa"
              : "Categorías de esta empresa"}
          </h2>
          {currentPage?.items.length === 0 && (
            <p>
              {kind === "products"
                ? "Todavía no hay productos."
                : "Todavía no hay categorías."}
            </p>
          )}
          <ul className="catalog-list">
            {currentPage?.items.map((item) => (
              <li key={item.id}>
                <div className="catalog-product-heading">
                  {isProduct(item) && (
                    <ProductThumbnail
                      actor={identity}
                      productId={item.id}
                      name={item.name}
                      onProblem={onProblem}
                    />
                  )}
                  <div>
                    <h3>{item.name}</h3>
                    <span>{item.is_active ? "Activo" : "Inactivo"}</span>
                  </div>
                </div>
                {isProduct(item) && (
                  <dl>
                    <div>
                      <dt>SKU</dt>
                      <dd>{item.sku ?? "Sin SKU"}</dd>
                    </div>
                    <div>
                      <dt>Precio de venta</dt>
                      <dd>{displayPrice(item.price)}</dd>
                    </div>
                  </dl>
                )}
              </li>
            ))}
          </ul>
          <div className="membership-pagination">
            <button
              className="action"
              disabled={busy}
              onClick={() => void page(kind)}
            >
              Volver al principio
            </button>
            {currentPage?.next_after && (
              <button
                className="action"
                disabled={busy}
                onClick={() => void page(kind, currentPage.next_after!)}
              >
                Siguiente página
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
