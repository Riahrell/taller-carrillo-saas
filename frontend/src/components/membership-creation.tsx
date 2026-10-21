"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { accessApi, type Role, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import { membershipsApi, roleNames, type NewUser } from "@/lib/memberships/api";

type Props = {
  session: SessionContext;
  onProblem: (message: string) => void;
  onWriting: (value: boolean) => void;
  onChanged: () => void;
};

export default function MembershipCreation({
  session,
  onProblem,
  onWriting,
  onChanged,
}: Props) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [stage, setStage] = useState<"edit" | "confirm" | "success">("edit");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const form = useRef<HTMLFormElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const hide = () => {
      if (document.visibilityState !== "hidden") return;
      form.current
        ?.querySelectorAll<HTMLInputElement>(
          'input[type="password"], input[autocomplete="one-time-code"]',
        )
        .forEach((field) => {
          field.value = "";
        });
    };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  }, []);

  useEffect(
    () => () => {
      controller.current?.abort();
      form.current?.reset();
      onWriting(false);
    },
    [onWriting],
  );
  useEffect(() => {
    const current = form.current;
    return () => current?.reset();
  }, [stage]);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
    else heading.current?.focus({ preventScroll: true });
  }, [stage, message]);
  function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !email.trim() || Array.from(email.trim()).length > 254 || !role)
      return;
    setEmail(email.trim().toLowerCase());
    setMessage("");
    setStage("confirm");
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current || !role) return;
    const fields = new FormData(event.currentTarget);
    const password = String(fields.get("password") ?? "");
    const confirmation = String(fields.get("confirmation") ?? "");
    event.currentTarget.reset();
    fields.delete("password");
    fields.delete("confirmation");
    if (
      password !== confirmation ||
      Array.from(password).length < 15 ||
      Array.from(password).length > 128
    ) {
      setMessage(
        "Las contraseñas deben coincidir y tener de 15 a 128 caracteres.",
      );
      return;
    }
    operation.current = true;
    const current = new AbortController();
    controller.current = current;
    const data: NewUser = { email, role, password };
    setBusy(true);
    setMessage("");
    onWriting(true);
    try {
      const created = await membershipsApi.create(session.membership_id, data, {
        signal: current.signal,
      });
      if (current.signal.aborted) return;
      onChanged();
      const context = await accessApi.session({ signal: current.signal });
      if (
        context.user_id !== session.user_id ||
        context.tenant_id !== session.tenant_id ||
        context.membership_id !== session.membership_id ||
        context.role !== "admin"
      )
        throw new Error("Changed context");
      const detail = await membershipsApi.detail(
        session.membership_id,
        created.membership_id,
        { signal: current.signal },
      );
      if (detail.role !== data.role || !detail.is_active)
        throw new Error("Changed result");
      if (!current.signal.aborted) {
        setEmail("");
        setRole("");
        setStage("success");
      }
    } catch (error) {
      if (current.signal.aborted) return;
      if (error instanceof ApiError && error.status === 428) {
        onProblem(
          "La confirmación de identidad venció. Comprueba el acceso y revisa el alta de nuevo.",
        );
      } else if (error instanceof ApiError && error.status === 409) {
        setMessage(
          "No se pudo crear este acceso con ese correo. Revisa el listado antes de intentar otra alta.",
        );
        setStage("edit");
      } else if (error instanceof ApiError && error.status === 422) {
        setMessage(
          "Revisa el correo y el perfil. La contraseña se ha retirado; vuelve a revisar el alta.",
        );
        setStage("edit");
      } else {
        onProblem(
          "No pudimos confirmar el resultado del alta. Comprueba el acceso y revisa el listado antes de volver a crear una cuenta.",
        );
      }
    } finally {
      data.password = "";
      onWriting(false);
      operation.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  return (
    <section
      className="membership-review membership-creation"
      aria-label="Alta asistida"
      aria-busy={busy}
    >
      <p className="panel-step">
        {stage === "edit"
          ? "DATOS DEL NUEVO ACCESO"
          : stage === "confirm"
            ? "REVISIÓN Y CONFIRMACIÓN"
            : "ALTA CONFIRMADA"}
      </p>
      <h2 ref={heading} tabIndex={-1}>
        {stage === "success"
          ? "Acceso creado"
          : stage === "confirm"
            ? "Comprueba los datos"
            : "Crear un acceso para tu equipo"}
      </h2>
      {message && (
        <p className="access-message" role="alert" ref={notice} tabIndex={-1}>
          {message}
        </p>
      )}
      {busy && <p role="status">Confirmando el alta…</p>}
      {stage === "success" ? (
        <>
          <p role="status">
            El acceso y su perfil se han comprobado en esta empresa. La persona
            ya puede iniciar sesión con la contraseña que eligió.
          </p>
          <Link className="action" href="/membresias">
            Ver membresías
          </Link>
        </>
      ) : stage === "edit" ? (
        <>
          <p className="field-help">
            Para una persona nueva, presente en un equipo de confianza. Los
            correos ya registrados requieren otro procedimiento; esta alta no
            cambia sus cuentas.
          </p>
          <form onSubmit={review} method="post" action="/membresias/nueva">
            <fieldset className="membership-fields">
              <legend>Correo y perfil de la persona nueva</legend>
              <label htmlFor="new-email">Correo del nuevo usuario</label>
              <input
                id="new-email"
                name="email"
                type="email"
                required
                autoComplete="off"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
              <label htmlFor="new-role">Perfil del nuevo usuario</label>
              <select
                id="new-role"
                name="role"
                required
                value={role}
                onChange={(event) => setRole(event.target.value as Role | "")}
              >
                <option value="" disabled>
                  Elige un perfil
                </option>
                {Object.entries(roleNames).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <button className="action" type="submit">
                Revisar alta
              </button>
            </fieldset>
          </form>
        </>
      ) : (
        <>
          <dl className="change-summary">
            <div>
              <dt>Correo</dt>
              <dd>{email}</dd>
            </div>
            <div>
              <dt>Perfil</dt>
              <dd>{role ? roleNames[role] : ""}</dd>
            </div>
            <div>
              <dt>Acceso</dt>
              <dd>Activo en esta empresa</dd>
            </div>
          </dl>
          <p className="field-help" id="new-password-help">
            La persona nueva debe elegir y escribir su contraseña aquí: de 15 a
            128 caracteres. No la compartas por chat ni correo. Esta operación
            no cambia tu sesión.
          </p>
          <form
            ref={form}
            onSubmit={(event) => void create(event)}
            method="post"
            action="/membresias/nueva"
          >
            <fieldset className="membership-fields" disabled={busy}>
              <legend>Contraseña elegida por la persona nueva</legend>
              <label htmlFor="new-password">Contraseña del nuevo usuario</label>
              <input
                id="new-password"
                name="password"
                type="password"
                autoComplete="new-password"
                required
                aria-describedby="new-password-help"
              />
              <label htmlFor="new-confirmation">Repetir contraseña</label>
              <input
                id="new-confirmation"
                name="confirmation"
                type="password"
                autoComplete="new-password"
                required
              />
              <button className="action" type="submit">
                Confirmar y crear acceso
              </button>
              <button
                className="text-action"
                type="button"
                onClick={() => {
                  form.current?.reset();
                  setMessage("");
                  setStage("edit");
                }}
              >
                Volver a editar
              </button>
            </fieldset>
          </form>
        </>
      )}
      {stage !== "success" && !busy && (
        <Link
          className="text-action"
          href="/membresias"
          onClick={() => form.current?.reset()}
        >
          Cancelar alta
        </Link>
      )}
    </section>
  );
}
