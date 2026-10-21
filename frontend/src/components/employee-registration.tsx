"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { registrationApi, type RegistrationStatus } from "@/lib/employees/api";
import { ApiError } from "@/lib/api/client";

export default function EmployeeRegistration() {
  const [stage, setStage] = useState<
    RegistrationStatus | "checking" | "form" | "missing"
  >("checking");
  const invitation = useRef("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [cooldown, setCooldown] = useState(0);
  const running = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    if (!invitation.current) {
      const match = /^#invitacion=([A-Za-z0-9_-]{43})$/.exec(location.hash);
      invitation.current = match?.[1] ?? "";
    }
    if (location.hash) history.replaceState(null, "", location.pathname);
    void registrationApi
      .prepare({ signal: current.signal })
      .then(() => registrationApi.status({ signal: current.signal }))
      .then((value) => {
        if (!current.signal.aborted) setStage(value);
      })
      .catch((error) => {
        if (!current.signal.aborted) {
          setStage(invitation.current ? "form" : "missing");
          if (!(error instanceof ApiError && error.status === 400))
            setMessage(
              "No pudimos comprobar el registro. Si ya tienes una sesión abierta, ciérrala antes de registrarte.",
            );
        }
      });
    return () => {
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    if (message) notice.current?.focus({ preventScroll: true });
    else heading.current?.focus({ preventScroll: true });
  }, [message, stage]);
  useEffect(() => {
    if (!cooldown) return;
    const timer = setTimeout(
      () => setCooldown((value) => Math.max(0, value - 1)),
      1000,
    );
    return () => clearTimeout(timer);
  }, [cooldown]);
  async function perform(action: () => Promise<RegistrationStatus | void>) {
    if (running.current) return;
    running.current = true;
    setBusy(true);
    setMessage("");
    const current = new AbortController();
    controller.current = current;
    try {
      const next = await action();
      if (!current.signal.aborted) {
        if (next) setStage(next);
        setCooldown(60);
        if (next === "awaiting_code") invitation.current = "";
      }
    } catch (error) {
      if (!current.signal.aborted) {
        if (error instanceof ApiError && error.status === 429) {
          setCooldown(60);
          setMessage(
            "Espera al menos un minuto antes de volver a intentarlo. Si sigue bloqueado, inténtalo más tarde.",
          );
        } else
          setMessage(
            "No pudimos confirmar la operación. Comprueba el código y la vigencia de la invitación. Si perdiste la respuesta, pulsa Comprobar registro antes de repetir.",
          );
      }
    } finally {
      running.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }
  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (running.current) return;
    const form = event.currentTarget,
      data = new FormData(form);
    const email = String(data.get("email") ?? ""),
      password = String(data.get("password") ?? "");
    if (password !== String(data.get("confirmation") ?? "")) {
      setMessage("Las contraseñas no coinciden.");
      return;
    }
    form.reset();
    void perform(() =>
      registrationApi.start(
        { email, password, invitation: invitation.current },
        { signal: controller.current?.signal },
      ),
    );
  }
  function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const code = String(new FormData(event.currentTarget).get("code") ?? "")
      .trim()
      .toUpperCase();
    event.currentTarget.reset();
    void perform(() =>
      registrationApi.confirm(code, { signal: controller.current?.signal }),
    );
  }
  return (
    <main id="contenido" className="memberships-workspace" tabIndex={-1}>
      <p className="eyebrow">INCORPORACIÓN AL EQUIPO</p>
      <h1 ref={heading} tabIndex={-1}>
        Crear mi cuenta
      </h1>
      {message && (
        <p role="alert" ref={notice} tabIndex={-1}>
          {message}
        </p>
      )}
      {stage === "checking" && <p role="status">Comprobando registro…</p>}
      {stage === "missing" && (
        <p>
          Abre la invitación privada que te compartió tu administrador. No
          puedes elegir una empresa desde esta página.
        </p>
      )}
      {stage === "form" && (
        <form className="catalog-form" onSubmit={start}>
          <p>
            Usa el correo al que está dirigida tu invitación. Verificarlo no
            concede acceso al inventario. Si ya tienes cuenta, inicia sesión y
            consulta a tu administrador; este formulario crea cuentas nuevas.
          </p>
          <label>
            Tu correo electrónico
            <input
              name="email"
              type="email"
              autoComplete="email"
              maxLength={254}
              required
              disabled={busy}
            />
          </label>
          <label>
            Crea tu contraseña
            <input
              name="password"
              type="password"
              autoComplete="new-password"
              minLength={15}
              maxLength={128}
              required
              disabled={busy}
            />
          </label>
          <p id="registration-password-help">
            Al menos 15 caracteres. Usa una contraseña única y poco común.
          </p>
          <label>
            Repite tu contraseña
            <input
              name="confirmation"
              type="password"
              autoComplete="new-password"
              minLength={15}
              maxLength={128}
              aria-describedby="registration-password-help"
              required
              disabled={busy}
            />
          </label>
          <div className="form-actions">
            <button className="action" disabled={busy}>
              Crear cuenta y verificar correo
            </button>
            <Link className="action secondary" href="/">
              Cancelar
            </Link>
          </div>
        </form>
      )}
      {stage === "awaiting_code" && (
        <form className="catalog-form" onSubmit={confirm}>
          <p>
            Enviamos un código al correo de la invitación. Tiene seis caracteres
            y caduca en diez minutos. Revisa también correo no deseado.
          </p>
          <label>
            Código del correo
            <input
              name="code"
              autoComplete="one-time-code"
              maxLength={6}
              minLength={6}
              pattern="[A-Za-z0-9]{6}"
              required
              disabled={busy}
            />
          </label>
          <div className="form-actions">
            <button className="action" disabled={busy}>
              Verificar mi correo
            </button>
            <button
              type="button"
              className="action secondary"
              disabled={busy || cooldown > 0}
              onClick={() =>
                void perform(() =>
                  registrationApi.resend({
                    signal: controller.current?.signal,
                  }),
                )
              }
            >
              {cooldown > 0 ? `Reenviar en ${cooldown} s` : "Reenviar código"}
            </button>
          </div>
        </form>
      )}
      {stage === "pending_approval" && (
        <section className="membership-notice">
          <h2>Correo verificado</h2>
          <p>
            Tu cuenta está pendiente de aprobación. Tu administrador debe
            autorizar la incorporación y los permisos antes de que puedas
            trabajar con inventario.
          </p>
        </section>
      )}
      {stage === "approved" && (
        <section className="membership-notice">
          <h2>Incorporación aprobada</h2>
          <p>
            Ya puedes iniciar sesión. El administrador asignará por separado los
            permisos para consultar o modificar inventario.
          </p>
        </section>
      )}
      {stage !== "checking" && (
        <div className="form-actions">
          <button
            type="button"
            className="action secondary"
            disabled={busy}
            onClick={() =>
              void perform(() =>
                registrationApi.status({ signal: controller.current?.signal }),
              )
            }
          >
            Comprobar registro
          </button>
          <Link href="/" className="text-action">
            Volver al inicio de sesión
          </Link>
        </div>
      )}
    </main>
  );
}
