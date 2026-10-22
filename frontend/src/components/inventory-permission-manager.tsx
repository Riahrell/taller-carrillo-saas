"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { sameContext } from "@/lib/access/same-context";
import {
  membershipsApi,
  roleNames,
  type Member,
  type Page,
} from "@/lib/memberships/api";
import {
  permissionsApi,
  type ReadPermission,
} from "@/lib/employees/permissions";
import { ApiError } from "@/lib/api/client";

type Selection = {
  member: Member;
  permission: ReadPermission;
  granted: boolean;
  review: boolean;
};
type Props = {
  session: SessionContext;
  onProblem: (message: string) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
};
export default function InventoryPermissionManager({
  session,
  onProblem,
  onWriting,
  onChanged,
}: Props) {
  const [page, setPage] = useState<Page | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const running = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const actor = session.membership_id;
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    void membershipsApi
      .page(actor, undefined, { signal: current.signal })
      .then((result) => {
        if (!current.signal.aborted) {
          setPage(result);
          setBusy(false);
        }
      })
      .catch(() => {
        if (!current.signal.aborted)
          onProblem("No pudimos consultar los accesos de esta empresa.");
      });
    return () => {
      controller.current?.abort();
      onWriting(false);
    };
  }, [actor, onProblem, onWriting]);
  useEffect(() => {
    if (selection || message) heading.current?.focus({ preventScroll: true });
  }, [selection, message]);
  async function select(member: Member) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    try {
      const permission = await permissionsApi.current(
        actor,
        member.membership_id,
        { signal: current.signal },
      );
      if (!current.signal.aborted)
        setSelection({
          member,
          permission,
          granted: permission.granted,
          review: false,
        });
    } catch {
      if (!current.signal.aborted)
        onProblem(
          "No pudimos comprobar el permiso vigente. Comprueba tu acceso.",
        );
    } finally {
      running.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  async function save() {
    if (!selection || running.current) return;
    const chosen = selection;
    running.current = true;
    setBusy(true);
    onWriting(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    try {
      const result = await permissionsApi.change(
        actor,
        chosen.member.membership_id,
        chosen.granted,
        chosen.permission.etag,
        { signal: current.signal },
      );
      const fresh = await accessApi.session({ signal: current.signal });
      if (current.signal.aborted) return;
      if (!sameContext(session, fresh)) {
        onProblem("La sesión cambió. Comprueba el acceso.");
        return;
      }
      setSelection({
        member: chosen.member,
        permission: result,
        granted: result.granted,
        review: false,
      });
      setMessage(
        result.effective
          ? "Consulta de inventario autorizada. El empleado puede abrir Consultar existencias desde su inicio."
          : "Permiso de consulta retirado. Se rechazará la siguiente petición del empleado.",
      );
      onChanged();
    } catch (error) {
      if (!current.signal.aborted) {
        if (error instanceof ApiError && error.status === 412) {
          setSelection(null);
          setMessage(
            "El permiso cambió mientras lo revisabas. Vuelve a seleccionarlo antes de modificarlo.",
          );
        } else
          onProblem(
            "No pudimos confirmar el cambio. Comprueba el acceso y vuelve a consultar el permiso antes de repetir.",
          );
      }
    } finally {
      running.current = false;
      onWriting(false);
      if (!current.signal.aborted) setBusy(false);
    }
  }
  async function load(after?: string) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    const current = new AbortController();
    controller.current = current;
    try {
      const result = await membershipsApi.page(actor, after, {
        signal: current.signal,
      });
      if (!current.signal.aborted) setPage(result);
    } catch {
      if (!current.signal.aborted)
        onProblem("No pudimos actualizar los accesos.");
    } finally {
      running.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section aria-label="Permisos de consulta de inventario">
      <p>
        Autoriza la consulta de productos activos, fotos, precio de venta y
        existencias. Los costos, la valoración y los cambios de inventario
        siguen reservados al administrador.
      </p>
      <p>
        Esta primera concesión se aplica al perfil Solo lectura. Los
        administradores conservan su acceso propio.
      </p>
      <Link className="text-action" href="/inventario/consulta">
        Ver pantalla de consulta
      </Link>
      {message && (
        <p role="status" className="access-message">
          {message}
        </p>
      )}
      {busy && <p role="status">Comprobando permisos…</p>}
      {selection ? (
        <section className="catalog-form inventory-permission-edit">
          <h2 tabIndex={-1} ref={heading}>
            {selection.review
              ? "Revisar cambio de permiso"
              : "Permiso de consulta"}
          </h2>
          <p>
            <strong>{selection.member.email}</strong> ·{" "}
            {roleNames[selection.member.role]}
          </p>
          {!selection.permission.eligible && (
            <p>
              Este acceso no está activo con perfil Solo lectura. Puedes retirar
              una concesión anterior; no conceder una nueva desde aquí.
            </p>
          )}
          {selection.review ? (
            <p>
              {selection.granted
                ? "Podrá consultar fotos, productos y existencias de esta empresa. No podrá ver costos ni registrar cambios."
                : "Dejará de poder consultar productos, existencias y fotos mediante este permiso. El servidor rechazará la siguiente petición."}
            </p>
          ) : (
            <label className="permission-toggle">
              <input
                type="checkbox"
                checked={selection.granted}
                disabled={
                  busy ||
                  (!selection.permission.eligible &&
                    !selection.permission.granted)
                }
                onChange={(event) =>
                  setSelection({ ...selection, granted: event.target.checked })
                }
              />{" "}
              Permitir consultar existencias
            </label>
          )}
          <div className="form-actions">
            <button
              className="action"
              disabled={
                busy ||
                selection.granted === selection.permission.granted ||
                (!selection.permission.eligible && selection.granted)
              }
              onClick={() =>
                selection.review
                  ? void save()
                  : setSelection({ ...selection, review: true })
              }
            >
              {selection.review ? "Confirmar permiso" : "Revisar cambio"}
            </button>
            <button
              className="action secondary"
              disabled={busy}
              onClick={() => setSelection(null)}
            >
              Cancelar, no cambiar nada
            </button>
          </div>
        </section>
      ) : (
        <>
          <h2 ref={heading} tabIndex={-1}>
            Accesos de esta empresa
          </h2>
          <ul className="employee-list">
            {page?.items.map((member) => (
              <li key={member.membership_id}>
                <div>
                  <strong>{member.email}</strong>
                  <p>
                    {roleNames[member.role]} ·{" "}
                    {member.is_active && member.user_is_active
                      ? "Activo"
                      : "Inactivo"}
                  </p>
                </div>
                <button
                  className="action secondary"
                  disabled={busy}
                  onClick={() => void select(member)}
                >
                  Revisar permiso de {member.email}
                </button>
              </li>
            ))}
          </ul>
          <div className="form-actions">
            <button
              className="action secondary"
              disabled={busy}
              onClick={() => void load()}
            >
              Inicio de accesos
            </button>
            <button
              className="action secondary"
              disabled={busy || !page?.next_after}
              onClick={() => void load(page?.next_after ?? undefined)}
            >
              Más accesos
            </button>
          </div>
        </>
      )}
    </section>
  );
}
