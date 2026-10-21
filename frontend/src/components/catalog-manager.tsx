"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import IdentityConfirmation from "@/components/identity-confirmation";
import ProductEditor from "@/components/product-editor";
import ProductSearch from "@/components/product-search";
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
  const [editingTarget, setEditingTarget] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);
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
  const [createdProduct, setCreatedProduct] = useState<{
    id: string;
    name: string;
  } | null>(null);
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
            : "La confirmación de identidad venció. Comprueba el acceso y revisa los datos de nuevo.",
        );
      },
      Math.max(0, deadline - performance.now()),
    );
    return () => clearTimeout(timer);
  }, [deadline, stage, onProblem]);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
    else if (stage !== "identity")
      heading.current?.focus({ preventScroll: true });
  }, [message, stage]);
  function reset() {
    setName("");
    setPrice("");
    setSku("");
    setSelected(null);
    setStage("closed");
  }
  async function page(
    collection: Kind,
    after?: string,
    term = search,
    category = categoryFilter,
  ) {
    if (operation.current) return;
    operation.current = true;
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    try {
      if (collection === "products") {
        const value = await catalogApi.products(
          identity,
          after,
          {
            signal: current.signal,
          },
          term,
          category,
        );
        if (!current.signal.aborted) {
          setProducts(value);
          setSearch(term);
          setCategoryFilter(category);
        }
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
        setSearch("");
        setCategoryFilter(null);
        setCategories(c);
        reset();
        setSuccess(true);
        setCreatedProduct(
          kind === "products" ? { id: created.id, name: created.name } : null,
        );
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
            ? "Ese SKU ya está en uso. Revisa el catálogo antes de registrar el producto."
            : "Revisa los datos antes de continuar.",
        );
      } else
        onProblem(
          error instanceof ApiError && error.status === 428
            ? "La confirmación de identidad venció. Comprueba el acceso y revisa los datos de nuevo."
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
            disabled={busy || stage !== "closed" || editingTarget !== null}
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
            disabled={busy || stage !== "closed" || editingTarget !== null}
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
          disabled={busy || stage !== "closed" || editingTarget !== null}
          onClick={() => {
            reset();
            setMessage("");
            setSuccess(false);
            setCreatedProduct(null);
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
      {kind === "products" && (
        <ProductSearch
          actor={identity}
          category={categoryFilter}
          value={search}
          disabled={busy || stage !== "closed" || editingTarget !== null}
          onSearch={(term, category) =>
            void page("products", undefined, term, category)
          }
        />
      )}
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
      {success && createdProduct && stage === "closed" && (
        <section className="membership-notice" aria-label="Completar producto">
          <h3>Agrega la imagen de {createdProduct.name}</h3>
          <p>
            El producto ya está guardado. Abre su ficha para tomar una foto o
            elegirla desde la galería.
          </p>
          <Link
            className="action"
            href={`/inventario?producto=${createdProduct.id}`}
          >
            Agregar imagen al producto creado
          </Link>
        </section>
      )}
      {busy && (
        <p role="status">
          {stage === "review" ? "Guardando…" : "Consultando…"}
        </p>
      )}
      {stage === "identity" && (
        <section aria-label="Autorizar registro">
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
            Cancelar, no guardar
          </button>
        </section>
      )}
      {(stage === "edit" || stage === "review") && (
        <section className="membership-review membership-creation">
          <h2 ref={heading} tabIndex={-1}>
            {stage === "review"
              ? kind === "products"
                ? "Revisa el producto"
                : "Revisa la categoría"
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
                      Crear el producto no registra existencias. Después de
                      guardarlo podrás agregar su imagen desde la ficha del
                      producto.
                    </p>
                  </>
                )}
                <button className="action primary" type="submit">
                  {kind === "products"
                    ? "Revisar producto"
                    : "Revisar categoría"}
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
                  {kind === "products"
                    ? "Registrar producto"
                    : "Registrar categoría"}
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
            Cancelar, no guardar
          </button>
        </section>
      )}
      {editingTarget && (
        <ProductEditor
          key={editingTarget}
          target={editingTarget}
          session={session}
          onWriting={onWriting}
          onChanged={onChanged}
          onProblem={onProblem}
          onClose={() => setEditingTarget(null)}
          onSaved={() => {
            setEditingTarget(null);
            setSuccess(false);
            setCreatedProduct(null);
            void page(kind).then(() => {
              if (alive.current) {
                setSuccess(true);
                setMessage("Cambios del producto guardados.");
              }
            });
          }}
        />
      )}
      <div
        inert={busy || stage !== "closed" || editingTarget !== null}
        aria-busy={busy}
      >
        <h2>
          {kind === "products"
            ? "Productos de esta empresa"
            : "Categorías de esta empresa"}
        </h2>
        {currentPage?.items.length === 0 && (
          <p>
            {kind === "products"
              ? search || categoryFilter
                ? "No se encontraron productos con esos filtros."
                : "Todavía no hay productos."
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
              {isProduct(item) && (
                <div className="form-actions catalog-item-actions">
                  <button
                    className="action"
                    aria-label={`Editar producto de ${item.name}`}
                    disabled={busy || editingTarget !== null}
                    onClick={() => {
                      setMessage("");
                      setSuccess(false);
                      setCreatedProduct(null);
                      setEditingTarget(item.id);
                    }}
                  >
                    Editar producto
                  </button>
                  <Link
                    className="action secondary"
                    href={`/inventario?producto=${item.id}`}
                  >
                    Ver inventario e imagen
                    <span className="sr-only"> de {item.name}</span>
                  </Link>
                </div>
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
      </div>
    </section>
  );
}
