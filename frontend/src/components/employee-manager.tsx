"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { sameContext } from "@/lib/access/same-context";
import {
  employeesApi,
  type Applicant,
  type Invitation,
  type Page,
} from "@/lib/employees/api";

type Review =
  | { kind: "invite"; email: string }
  | { kind: "revoke"; item: Invitation }
  | { kind: "approve"; item: Applicant };
type Props = {
  session: SessionContext;
  onProblem: (message: string) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
};
const when = (value: string) =>
  new Intl.DateTimeFormat("es-SV", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "America/El_Salvador",
  }).format(new Date(value));
export default function EmployeeManager({
  session,
  onProblem,
  onWriting,
  onChanged,
}: Props) {
  const [invites, setInvites] = useState<Page<Invitation> | null>(null);
  const [pending, setPending] = useState<Page<Applicant> | null>(null);
  const [busy, setBusy] = useState(true);
  const [review, setReview] = useState<Review | null>(null);
  const [link, setLink] = useState("");
  const [message, setMessage] = useState("");
  const running = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const focus = useRef<HTMLHeadingElement>(null);
  const actor = session.membership_id;
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    void Promise.all([
      employeesApi.invitations(actor, undefined, { signal: current.signal }),
      employeesApi.pending(actor, undefined, { signal: current.signal }),
    ])
      .then(([first, second]) => {
        if (!current.signal.aborted) {
          setInvites(first);
          setPending(second);
          setBusy(false);
        }
      })
      .catch(() => {
        if (!current.signal.aborted)
          onProblem(
            "No pudimos consultar las invitaciones y solicitudes. Comprueba tu acceso.",
          );
      });
    const hide = () => {
      if (document.visibilityState === "hidden") setLink("");
    };
    document.addEventListener("visibilitychange", hide);
    return () => {
      controller.current?.abort();
      onWriting(false);
      document.removeEventListener("visibilitychange", hide);
    };
  }, [actor, onProblem, onWriting]);
  useEffect(() => {
    if (review || message) focus.current?.focus({ preventScroll: true });
  }, [review, message]);
  async function pages(kind: "invitations" | "pending", after?: string) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    const current = new AbortController();
    controller.current = current;
    try {
      if (kind === "invitations") {
        const result = await employeesApi.invitations(actor, after, {
          signal: current.signal,
        });
        if (!current.signal.aborted) setInvites(result);
      } else {
        const result = await employeesApi.pending(actor, after, {
          signal: current.signal,
        });
        if (!current.signal.aborted) setPending(result);
      }
    } catch {
      if (!current.signal.aborted)
        onProblem("No pudimos actualizar la lista. Comprueba tu acceso.");
    } finally {
      running.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  function propose(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const email = String(new FormData(event.currentTarget).get("email") ?? "")
      .trim()
      .toLowerCase();
    setLink("");
    setMessage("");
    setReview({ kind: "invite", email });
  }
  async function confirm() {
    if (!review || running.current) return;
    const chosen = review;
    running.current = true;
    setBusy(true);
    setMessage("");
    setLink("");
    onWriting(true);
    const current = new AbortController();
    controller.current = current;
    try {
      let createdLink = "";
      if (chosen.kind === "invite") {
        const created = await employeesApi.invite(actor, chosen.email, {
          signal: current.signal,
        });
        createdLink = `${location.origin}/registro#invitacion=${created.code}`;
      } else if (chosen.kind === "revoke")
        await employeesApi.revoke(actor, chosen.item.id, {
          signal: current.signal,
        });
      else
        await employeesApi.approve(actor, chosen.item.id, {
          signal: current.signal,
        });
      const fresh = await accessApi.session({ signal: current.signal });
      if (current.signal.aborted) return;
      if (!sameContext(session, fresh)) {
        onProblem("La sesión cambió. Comprueba tu acceso antes de continuar.");
        return;
      }
      const [first, second] = await Promise.all([
        employeesApi.invitations(actor, undefined, { signal: current.signal }),
        employeesApi.pending(actor, undefined, { signal: current.signal }),
      ]);
      if (current.signal.aborted) return;
      setInvites(first);
      setPending(second);
      setReview(null);
      setLink(createdLink);
      setMessage(
        chosen.kind === "invite"
          ? "Invitación creada. Comparte el enlace privado únicamente con esa persona."
          : chosen.kind === "revoke"
            ? "Invitación revocada. Esto no desactiva una cuenta que ya fue aprobada."
            : "Incorporación aprobada sin permisos de inventario. Puedes revisar la cuenta en Membresías.",
      );
      onChanged();
    } catch {
      if (!current.signal.aborted)
        onProblem(
          "No pudimos confirmar el resultado. Comprueba el acceso y revisa las listas antes de repetir. Si perdiste el enlace, revoca esa invitación y crea otra.",
        );
    } finally {
      running.current = false;
      onWriting(false);
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section aria-label="Invitaciones y solicitudes">
      {message && (
        <>
          <h2 ref={focus} tabIndex={-1}>
            Resultado
          </h2>
          <p role="status">{message}</p>
        </>
      )}
      {link && (
        <div className="catalog-form">
          <label>
            Enlace privado de registro
            <input
              readOnly
              value={link}
              onFocus={(event) => event.currentTarget.select()}
            />
          </label>
          <p>
            Vence en 24 horas. El enlace se muestra una sola vez y se oculta al
            cambiar de pestaña. No concede permisos por sí solo.
          </p>
          <button
            className="action secondary"
            onClick={() => {
              void navigator.clipboard
                .writeText(link)
                .then(() =>
                  setMessage(
                    "Enlace copiado. Compártelo únicamente con la persona invitada.",
                  ),
                )
                .catch(() =>
                  setMessage("Selecciona y copia el enlace manualmente."),
                );
            }}
          >
            Copiar enlace privado
          </button>
        </div>
      )}
      {review ? (
        <section className="catalog-form">
          <h2 ref={focus} tabIndex={-1}>
            {review.kind === "invite"
              ? "Revisar invitación"
              : review.kind === "revoke"
                ? "Revisar revocación"
                : "Revisar incorporación"}
          </h2>
          <dl>
            <dt>Correo del empleado</dt>
            <dd>
              {review.kind === "invite" ? review.email : review.item.email}
            </dd>
          </dl>
          <p>
            {review.kind === "invite"
              ? "La persona deberá verificar ese correo y esperar tu aprobación. No podrá elegir empresa ni rol."
              : review.kind === "revoke"
                ? "Este enlace dejará de permitir el registro o la aprobación pendiente. Las cuentas ya aprobadas se administran por separado."
                : "Su correo ya está verificado. Al aprobar podrá iniciar sesión, pero seguirá sin permiso para consultar o modificar inventario."}
          </p>
          <div className="form-actions">
            <button
              className="action"
              disabled={busy}
              onClick={() => void confirm()}
            >
              {review.kind === "invite"
                ? "Crear invitación"
                : review.kind === "revoke"
                  ? "Revocar invitación"
                  : "Aprobar incorporación"}
            </button>
            <button
              className="action secondary"
              disabled={busy}
              onClick={() => setReview(null)}
            >
              Cancelar, no cambiar nada
            </button>
          </div>
        </section>
      ) : (
        <form className="catalog-form" onSubmit={propose}>
          <h2>Invitar empleado</h2>
          <label>
            Correo del empleado
            <input
              name="email"
              type="email"
              autoComplete="off"
              maxLength={254}
              required
              disabled={busy}
            />
          </label>
          <p>
            Comparte el enlace que se generará solo con la persona que usa ese
            correo. La incorporación requiere tu aprobación.
          </p>
          <div className="form-actions">
            <button className="action" disabled={busy}>
              Revisar invitación
            </button>
          </div>
        </form>
      )}
      {busy && <p role="status">Comprobando operación…</p>}
      <div inert={busy || review !== null}>
        <h2>Solicitudes con correo verificado</h2>
        <p>Aprobar la incorporación no concede permisos de inventario.</p>
        {pending?.items.length === 0 && (
          <p>No hay solicitudes pendientes de aprobación.</p>
        )}
        <ul className="employee-list">
          {pending?.items.map((item) => (
            <li key={item.id}>
              <div>
                <strong>{item.email}</strong>
                <p>Correo verificado: {when(item.verified_at)}</p>
              </div>
              <button
                className="action"
                disabled={busy || !!review}
                onClick={() => {
                  setLink("");
                  setReview({ kind: "approve", item });
                }}
              >
                Revisar incorporación de {item.email}
              </button>
            </li>
          ))}
        </ul>
        <div className="form-actions">
          <button
            className="action secondary"
            disabled={busy || !!review}
            onClick={() => void pages("pending")}
          >
            Actualizar solicitudes
          </button>
          <button
            className="action secondary"
            disabled={busy || !!review || !pending?.next_after}
            onClick={() =>
              void pages("pending", pending?.next_after ?? undefined)
            }
          >
            Más solicitudes
          </button>
        </div>
        <h2>Invitaciones</h2>
        <p>
          Incluye invitaciones vencidas y revocadas. Para invitar de nuevo al
          mismo correo, revoca primero la anterior.
        </p>
        {invites?.items.length === 0 && <p>Aún no hay invitaciones.</p>}
        <ul className="employee-list">
          {invites?.items.map((item) => (
            <li key={item.id}>
              <div>
                <strong>{item.email}</strong>
                <p>
                  {item.status === "revoked"
                    ? "Revocada"
                    : item.status === "expired"
                      ? "Vencida"
                      : "Pendiente"}{" "}
                  · Vencimiento: {when(item.expires_at)}
                </p>
              </div>
              <button
                className="action secondary"
                disabled={busy || !!review || item.status === "revoked"}
                onClick={() => {
                  setLink("");
                  setReview({ kind: "revoke", item });
                }}
              >
                Revisar invitación de {item.email}
              </button>
            </li>
          ))}
        </ul>
        <div className="form-actions">
          <button
            className="action secondary"
            disabled={busy || !!review}
            onClick={() => void pages("invitations")}
          >
            Actualizar invitaciones
          </button>
          <button
            className="action secondary"
            disabled={busy || !!review || !invites?.next_after}
            onClick={() =>
              void pages("invitations", invites?.next_after ?? undefined)
            }
          >
            Más invitaciones
          </button>
        </div>
      </div>
    </section>
  );
}
