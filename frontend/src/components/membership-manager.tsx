"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { accessApi, type Role, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import {
  membershipsApi,
  roleNames,
  type Detail,
  type Member,
  type Page,
} from "@/lib/memberships/api";

type Review = {
  member: Member;
  detail: Detail;
  role: Role;
  active: boolean;
  stage: "edit" | "confirm" | "blocked";
  message?: string;
};
type Props = {
  session: SessionContext;
  onProblem: (message: string) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
  onRecheck: () => void;
};
export default function MembershipManager({
  session,
  onProblem,
  onWriting,
  onChanged,
  onRecheck,
}: Props) {
  const [page, setPage] = useState<Page | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const operation = useRef(true);
  const controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const identity = session.membership_id;
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    void membershipsApi
      .page(identity, undefined, { signal: current.signal })
      .then((value) => {
        if (!current.signal.aborted) {
          setPage(value);
          setBusy(false);
          operation.current = false;
        }
      })
      .catch(() => {
        if (!current.signal.aborted)
          onProblem(
            "No pudimos consultar las membresías. Comprueba el acceso antes de continuar.",
          );
      });
    return () => {
      controller.current?.abort();
      onWriting(false);
    };
  }, [identity, onProblem, onWriting]);
  const reviewMessage = review?.message;
  const reviewId = review?.member.membership_id;
  const reviewStage = review?.stage;
  useEffect(() => {
    if (reviewMessage || message) notice.current?.focus();
    else if (reviewId || page) heading.current?.focus();
  }, [reviewStage, reviewId, reviewMessage, page, message]);
  function begin() {
    if (operation.current) return null;
    operation.current = true;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setMessage("");
    return current;
  }
  function finish(current: AbortController) {
    operation.current = false;
    if (!current.signal.aborted) setBusy(false);
  }
  async function showPage(after?: string) {
    const current = begin();
    if (!current) return;
    setPage(null);
    setReview(null);
    try {
      const value = await membershipsApi.page(identity, after, {
        signal: current.signal,
      });
      if (!current.signal.aborted) setPage(value);
    } catch {
      if (!current.signal.aborted)
        onProblem(
          "No pudimos consultar las membresías. Comprueba el acceso antes de continuar.",
        );
    } finally {
      finish(current);
    }
  }
  async function inspect(member: Member) {
    const current = begin();
    if (!current) return;
    setReview(null);
    try {
      const detail = await membershipsApi.detail(
        identity,
        member.membership_id,
        { signal: current.signal },
      );
      if (!current.signal.aborted)
        setReview({
          member,
          detail,
          role: detail.role,
          active: detail.is_active,
          stage: "edit",
        });
    } catch {
      if (!current.signal.aborted)
        onProblem(
          "No pudimos revisar ese acceso. Comprueba tu sesión antes de continuar.",
        );
    } finally {
      finish(current);
    }
  }
  async function save() {
    if (!review || review.stage !== "confirm") return;
    const current = begin();
    if (!current) return;
    const original = review;
    onWriting(true);
    try {
      await membershipsApi.change(
        identity,
        original.member.membership_id,
        { role: original.role, is_active: original.active },
        original.detail.etag,
        { signal: current.signal },
      );
      if (current.signal.aborted) return;
      onChanged();
      setReview(null);
      setPage(null);
      if (original.member.membership_id === identity) {
        onWriting(false);
        onRecheck();
        return;
      }
      const context = await accessApi.session({ signal: current.signal });
      if (
        context.user_id !== session.user_id ||
        context.tenant_id !== session.tenant_id ||
        context.membership_id !== identity ||
        context.role !== "admin"
      )
        throw new Error("Changed context");
      const refreshed = await membershipsApi.page(identity, undefined, {
        signal: current.signal,
      });
      if (!current.signal.aborted) {
        setPage(refreshed);
        setMessage("Cambio confirmado. El listado se ha actualizado.");
      }
    } catch (error) {
      if (current.signal.aborted) return;
      if (error instanceof ApiError && [409, 412].includes(error.status ?? 0)) {
        setReview({
          ...original,
          stage: "blocked",
          message:
            error.status === 409
              ? "Debe quedar al menos un administrador activo. Revisa el estado actual antes de continuar."
              : "Este acceso cambió desde tu revisión. Consulta el estado actual y revisa el cambio de nuevo.",
        });
      } else
        onProblem(
          "No pudimos confirmar el resultado del cambio. Comprueba el acceso y revisa la membresía antes de volver a modificarla.",
        );
    } finally {
      onWriting(false);
      finish(current);
    }
  }
  const changed =
    review &&
    (review.role !== review.detail.role ||
      review.active !== review.detail.is_active);
  function reviewChange(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busy && review && changed) setReview({ ...review, stage: "confirm" });
  }
  return (
    <section
      className="membership-content"
      aria-busy={busy}
      aria-label="Administración de accesos"
    >
      {busy && (
        <p role="status">
          {review?.stage === "confirm"
            ? "Confirmando el cambio…"
            : "Consultando accesos…"}
        </p>
      )}
      {message && (
        <p className="access-message" role="status" ref={notice} tabIndex={-1}>
          {message}
        </p>
      )}
      {review ? (
        <section className="membership-review">
          <p className="panel-step">
            {review.stage === "confirm"
              ? "CONFIRMACIÓN"
              : "REVISIÓN DEL ACCESO"}
          </p>
          <h2 ref={heading} tabIndex={-1}>
            {review.member.email}
          </h2>
          {review.message && (
            <p
              className="access-message"
              role="alert"
              ref={notice}
              tabIndex={-1}
            >
              {review.message}
            </p>
          )}
          {review.stage === "blocked" ? (
            <button
              className="action"
              disabled={busy}
              onClick={() => void inspect(review.member)}
            >
              Revisar estado actual
            </button>
          ) : (
            <form onSubmit={reviewChange} method="post" action="/membresias">
              <fieldset disabled={busy} className="membership-fields">
                <legend>
                  {review.stage === "edit"
                    ? "Nuevo estado de la membresía"
                    : "Comprueba el cambio antes de confirmarlo"}
                </legend>
                {review.stage === "edit" ? (
                  <>
                    <label htmlFor="membership-role">Perfil</label>
                    <select
                      id="membership-role"
                      value={review.role}
                      onChange={(event) =>
                        setReview({
                          ...review,
                          role: event.target.value as Role,
                        })
                      }
                    >
                      {Object.entries(roleNames).map(([role, label]) => (
                        <option key={role} value={role}>
                          {label}
                        </option>
                      ))}
                    </select>
                    <label className="membership-check">
                      <input
                        type="checkbox"
                        checked={review.active}
                        onChange={(event) =>
                          setReview({ ...review, active: event.target.checked })
                        }
                      />
                      Acceso activo a esta empresa
                    </label>
                    {!review.member.user_is_active && (
                      <p className="field-help">
                        La cuenta está inactiva. Activar esta membresía no
                        habilita la cuenta.
                      </p>
                    )}
                    <button
                      className="action"
                      type="submit"
                      disabled={!changed}
                    >
                      Revisar cambio
                    </button>
                  </>
                ) : (
                  <>
                    <dl className="change-summary">
                      <div>
                        <dt>Perfil</dt>
                        <dd>
                          {roleNames[review.detail.role]} →{" "}
                          {roleNames[review.role]}
                        </dd>
                      </div>
                      <div>
                        <dt>Acceso a esta empresa</dt>
                        <dd>
                          {review.detail.is_active ? "Activo" : "Inactivo"} →{" "}
                          {review.active ? "Activo" : "Inactivo"}
                        </dd>
                      </div>
                    </dl>
                    <p className="field-help">
                      Se revocarán las sesiones de esta membresía.
                    </p>
                    {review.member.membership_id === identity && (
                      <p className="access-message">
                        Este cambio cerrará tu sesión.
                      </p>
                    )}
                    <button
                      className="action"
                      type="button"
                      onClick={() => void save()}
                    >
                      Confirmar cambio
                    </button>
                    <button
                      className="text-action"
                      type="button"
                      onClick={() => setReview({ ...review, stage: "edit" })}
                    >
                      Volver a editar
                    </button>
                  </>
                )}
              </fieldset>
            </form>
          )}
          <button
            className="text-action"
            disabled={busy}
            onClick={() => {
              setReview(null);
              setMessage("");
            }}
          >
            Cancelar revisión
          </button>
        </section>
      ) : (
        page && (
          <>
            <div className="membership-toolbar">
              <h2 ref={heading} tabIndex={-1}>
                Accesos de la empresa
              </h2>
              <button
                className="text-action"
                disabled={busy}
                onClick={() => void showPage()}
              >
                Actualizar listado
              </button>
            </div>
            {page.items.length === 0 ? (
              <p>No hay accesos en esta página.</p>
            ) : (
              <ul className="membership-list">
                {page.items.map((member) => (
                  <li key={member.membership_id}>
                    <div className="membership-person">
                      <strong>{member.email}</strong>
                      {member.membership_id === identity && (
                        <span className="membership-badge">Tu acceso</span>
                      )}
                    </div>
                    <dl>
                      <div>
                        <dt>Perfil</dt>
                        <dd>{roleNames[member.role]}</dd>
                      </div>
                      <div>
                        <dt>Acceso</dt>
                        <dd>{member.is_active ? "Activo" : "Inactivo"}</dd>
                      </div>
                      <div>
                        <dt>Cuenta</dt>
                        <dd>{member.user_is_active ? "Activa" : "Inactiva"}</dd>
                      </div>
                    </dl>
                    <button
                      className="action secondary"
                      disabled={busy}
                      aria-label={`Revisar acceso de ${member.email}`}
                      onClick={() => void inspect(member)}
                    >
                      Revisar acceso
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <nav
              className="membership-pagination"
              aria-label="Páginas de membresías"
            >
              <button
                className="text-action"
                disabled={busy}
                onClick={() => void showPage()}
              >
                Volver al principio
              </button>
              <button
                className="action"
                disabled={busy || !page.next_after}
                onClick={() => {
                  if (page.next_after) void showPage(page.next_after);
                }}
              >
                Página siguiente
              </button>
            </nav>
            <p className="field-help">
              Hasta 25 accesos por página. El estado se comprueba de nuevo al
              revisar una membresía.
            </p>
          </>
        )
      )}
    </section>
  );
}
