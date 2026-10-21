"use client";

import Link from "next/link";
import EmailLoginFlow from "@/components/email-login-flow";
import MfaFlow from "@/components/mfa-flow";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";

import {
  accessApi,
  type Choice,
  type Role,
  type SessionContext,
} from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

type View =
  | { kind: "checking" }
  | { kind: "login"; message?: string }
  | { kind: "mfa"; stage: "mfa_totp" | "mfa_enrollment" | "email_code" }
  | { kind: "choose"; choices: Choice[] }
  | { kind: "active"; session: SessionContext }
  | { kind: "uncertain"; message: string; closing: boolean };

const roles: Record<Role, string> = {
  admin: "Administrador",
  cashier: "Cajero",
  mechanic: "Mecánico",
  read_only: "Solo lectura",
};

function failure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "csrf") return error.message;
    if (error.status === 429)
      return "Hay demasiadas solicitudes. Espera un momento antes de volver a intentar.";
    if (error.status === 503)
      return "El servicio no está disponible en este momento. Vuelve a comprobar el acceso.";
    if (error.status === 403)
      return "No pudimos validar el acceso. Vuelve a comprobarlo antes de continuar.";
  }
  return "No pudimos confirmar el resultado. Comprueba el acceso antes de volver a intentar.";
}

async function loadView(signal: AbortSignal): Promise<View> {
  try {
    return { kind: "active", session: await accessApi.session({ signal }) };
  } catch (error) {
    if (signal.aborted) return { kind: "checking" };
    if (error instanceof ApiError && error.status === 401) {
      try {
        await accessApi.prepare({ signal });
        return { kind: "login" };
      } catch (error) {
        return { kind: "uncertain", message: failure(error), closing: false };
      }
    }
    return { kind: "uncertain", message: failure(error), closing: false };
  }
}

export default function AccessPanel() {
  const [view, setView] = useState<View>({ kind: "checking" });
  const [busy, setBusy] = useState(false);
  const [visible, setVisible] = useState(false);
  const [selected, setSelected] = useState("");
  const [suspended, setSuspended] = useState(false);
  const currentView = useRef<View>(view);
  const loginForm = useRef<HTMLFormElement>(null);
  const operation = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const channel = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    currentView.current = view;
  }, [view]);

  const restore = useCallback(async (signal: AbortSignal) => {
    const next = await loadView(signal);
    if (!signal.aborted) setView(next);
  }, []);

  const check = useCallback(() => {
    if (operation.current) return;
    setView({ kind: "checking" });
    setSelected("");
    controller.current?.abort();
    controller.current = new AbortController();
    void restore(controller.current.signal);
  }, [restore]);

  useEffect(() => {
    const initial = new AbortController();
    controller.current = initial;
    void loadView(initial.signal).then((next) => {
      if (!initial.signal.aborted) setView(next);
    });
    const onFocus = () => {
      // Reconcile cookies across tabs and history restoration without polling.
      if (
        document.visibilityState === "visible" &&
        !["login", "mfa", "choose"].includes(currentView.current.kind)
      )
        check();
    };
    const onVisibility = () => {
      setSuspended(document.visibilityState === "hidden");
      if (document.visibilityState === "hidden") {
        const password = loginForm.current?.elements.namedItem("password");
        if (password instanceof HTMLInputElement) password.value = "";
        setVisible(false);
      } else onFocus();
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener("pageshow", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    if (typeof BroadcastChannel !== "undefined") {
      const current = new BroadcastChannel("carrillo_access");
      channel.current = current;
      current.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data === "changed") check();
      };
    }
    return () => {
      controller.current?.abort();
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("pageshow", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
      channel.current?.close();
      channel.current = null;
    };
  }, [check]);

  const message =
    view.kind === "login" || view.kind === "uncertain"
      ? view.message
      : undefined;
  useEffect(() => {
    if (message) notice.current?.focus();
    else if (view.kind === "active" || view.kind === "choose")
      title.current?.focus();
  }, [message, view.kind]);

  useEffect(() => {
    if (view.kind !== "active" && view.kind !== "choose") return;
    const choosing = view.kind === "choose";
    // Only purge the view. Never refresh an idle server session with a timer.
    const timeout = setTimeout(
      () => {
        if (operation.current) return;
        setSelected("");
        setView(
          choosing
            ? {
                kind: "login",
                message:
                  "El acceso pendiente ha caducado. Inicia sesión de nuevo.",
              }
            : {
                kind: "uncertain",
                message: "Vuelve a comprobar tu acceso para continuar.",
                closing: false,
              },
        );
      },
      (choosing ? 5 : 30) * 60_000,
    );
    return () => clearTimeout(timeout);
  }, [view]);

  async function act(
    task: "login" | "select" | "logout",
    credentials?: { email: string; password: string },
  ) {
    if (operation.current) return;
    operation.current = true;
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setBusy(true);
    setVisible(false);
    if (task === "logout") setView({ kind: "checking" });
    try {
      if (task === "logout") {
        await accessApi.logout({ signal: current.signal });
        channel.current?.postMessage("changed");
        await accessApi.prepare({ signal: current.signal });
        if (!current.signal.aborted) {
          setSelected("");
          setView({ kind: "login", message: "Sesión cerrada." });
        }
      } else {
        const result =
          task === "login" && credentials
            ? await accessApi.login(credentials.email, credentials.password, {
                signal: current.signal,
              })
            : await accessApi.select(selected, { signal: current.signal });
        if (current.signal.aborted) return;
        if (
          result.status === "mfa_totp" ||
          result.status === "mfa_enrollment" ||
          result.status === "email_code"
        ) {
          setView({ kind: "mfa", stage: result.status });
        } else if (result.status === "select_membership") {
          setSelected("");
          setView({ kind: "choose", choices: result.choices });
        } else {
          channel.current?.postMessage("changed");
          setView({ kind: "checking" });
          setSelected("");
          await restore(current.signal);
        }
      }
    } catch (error) {
      if (current.signal.aborted) return;
      setSelected("");
      if (
        error instanceof ApiError &&
        error.status === 401 &&
        task !== "logout"
      ) {
        setView({
          kind: "login",
          message:
            task === "login"
              ? "No pudimos iniciar sesión. Revisa tus datos o inténtalo más tarde."
              : "El acceso pendiente ya no es válido. Inicia sesión de nuevo.",
        });
      } else
        setView({
          kind: "uncertain",
          message: failure(error),
          closing: task === "logout",
        });
    } finally {
      operation.current = false;
      if (!current.signal.aborted) setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const email = String(data.get("email") ?? "");
    const password = String(data.get("password") ?? "");
    const input = form.elements.namedItem("password");
    if (input instanceof HTMLInputElement) input.value = "";
    setVisible(false);
    if (!password || Array.from(password).length > 128) {
      setView({
        kind: "login",
        message: "Escribe una contraseña de entre 1 y 128 caracteres.",
      });
      return;
    }
    void act("login", { email, password });
  }

  const heading =
    view.kind === "choose"
      ? "Selecciona una empresa"
      : view.kind === "active"
        ? "Sesión activa"
        : view.kind === "uncertain"
          ? "Comprueba tu acceso"
          : "Iniciar sesión";

  return (
    <section
      className="access-panel"
      aria-labelledby="access-title"
      aria-busy={busy || view.kind === "checking"}
    >
      <span className="panel-step">TU ESPACIO DE TRABAJO</span>
      <h2 id="access-title" ref={title} tabIndex={-1}>
        {heading}
      </h2>
      <div hidden={suspended} inert={suspended}>
        {message && (
          <p
            className="access-message"
            role={
              view.kind === "login" && message === "Sesión cerrada."
                ? "status"
                : "alert"
            }
            ref={notice}
            tabIndex={-1}
          >
            {message}
          </p>
        )}
        {view.kind === "mfa" && view.stage === "email_code" && (
          <EmailLoginFlow
            onCancel={() => void act("logout")}
            onLogin={(result) => {
              if (result.status === "select_membership") {
                setView({ kind: "choose", choices: result.choices });
              } else if (result.status === "authenticated") {
                check();
              }
            }}
          />
        )}
        {view.kind === "mfa" && view.stage !== "email_code" && (
          <MfaFlow
            initial={view.stage}
            onCancel={() => void act("logout")}
            onLogin={(result) => {
              if (result.status === "select_membership") {
                setView({ kind: "choose", choices: result.choices });
              } else if (result.status === "authenticated") {
                check();
              }
            }}
          />
        )}
        {view.kind === "login" && (
          <Link href="/recuperar">Olvidé mi contraseña</Link>
        )}
        {view.kind === "checking" && (
          <p role="status" className="panel-description">
            {busy ? "Confirmando el cambio…" : "Comprobando acceso…"}
          </p>
        )}
        {view.kind === "login" && (
          <form ref={loginForm} onSubmit={submit} method="post" action="/">
            <p className="panel-description">
              Ingresa con tu cuenta de Taller Carrillo.
            </p>
            <fieldset disabled={busy} className="login-fields">
              <legend className="sr-only">Datos de acceso</legend>
              <label htmlFor="email">Correo electrónico</label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                inputMode="email"
                spellCheck={false}
                required
                maxLength={254}
              />
              <label htmlFor="password">Contraseña</label>
              <div className="password-field">
                <input
                  id="password"
                  name="password"
                  type={visible ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  aria-describedby="password-help"
                />
                <button
                  type="button"
                  className="password-toggle"
                  aria-pressed={visible}
                  aria-label={
                    visible ? "Ocultar contraseña" : "Mostrar contraseña"
                  }
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? "Ocultar" : "Mostrar"}
                </button>
              </div>
              <span id="password-help" className="field-help">
                Tu contraseña distingue mayúsculas y minúsculas.
              </span>
              <button type="submit" className="action primary">
                {busy ? "Ingresando…" : "Iniciar sesión"}
                <span aria-hidden="true">↗</span>
              </button>
            </fieldset>
          </form>
        )}
        {view.kind === "choose" && (
          <form
            method="post"
            action="/"
            onSubmit={(event) => {
              event.preventDefault();
              if (
                selected &&
                view.choices.some((choice) => choice.membership_id === selected)
              )
                void act("select");
            }}
          >
            <p className="panel-description">
              Elige la empresa en la que vas a trabajar.
            </p>
            <fieldset disabled={busy} className="company-choices">
              <legend className="sr-only">Empresas disponibles</legend>
              {view.choices.map((choice) => (
                <label className="company-choice" key={choice.membership_id}>
                  <input
                    type="radio"
                    name="membership_id"
                    value={choice.membership_id}
                    checked={selected === choice.membership_id}
                    onChange={() => setSelected(choice.membership_id)}
                    required
                  />
                  <span>{choice.tenant_name}</span>
                </label>
              ))}
              <button
                type="submit"
                className="action primary"
                disabled={!selected}
              >
                {busy ? "Confirmando…" : "Continuar"}
                <span aria-hidden="true">↗</span>
              </button>
            </fieldset>
            <button
              type="button"
              className="text-action"
              disabled={busy}
              onClick={() => void act("logout")}
            >
              Cancelar acceso
            </button>
          </form>
        )}
        {view.kind === "active" && (
          <Link href="/seguridad">Seguridad de mi cuenta</Link>
        )}
        {view.kind === "active" && (
          <>
            <p className="panel-description">
              Tu sesión está activa en esta empresa.
            </p>
            <dl className="session-details">
              <div>
                <dt>Empresa</dt>
                <dd>{view.session.tenant_name}</dd>
              </div>
              <div>
                <dt>Perfil</dt>
                <dd>{roles[view.session.role]}</dd>
              </div>
            </dl>
            {view.session.role === "admin" && (
              <Link className="action membership-link" href="/inventario">
                Consultar inventario
              </Link>
            )}
            {view.session.role === "admin" && (
              <Link className="action membership-link" href="/catalogo">
                Administrar catálogo
              </Link>
            )}
            {view.session.role === "admin" && (
              <Link className="action membership-link" href="/membresias">
                Administrar membresías
              </Link>
            )}
            <button
              type="button"
              className="action primary"
              disabled={busy}
              onClick={() => void act("logout")}
            >
              Cerrar sesión<span aria-hidden="true">↗</span>
            </button>
            <p className="field-help session-help">
              Para acceder con otra cuenta o empresa, cierra la sesión.
            </p>
          </>
        )}
        {view.kind === "uncertain" && (
          <div className="recovery-actions">
            <button
              type="button"
              className="action primary"
              disabled={busy}
              onClick={check}
            >
              Comprobar acceso
            </button>
            {view.closing && (
              <button
                type="button"
                className="text-action"
                disabled={busy}
                onClick={() => void act("logout")}
              >
                Reintentar cierre
              </button>
            )}
          </div>
        )}
        <p className="access-footnote">
          <span className="status-dot" aria-hidden="true" />
          Acceso reservado al personal autorizado
        </p>
        <noscript>Activa JavaScript para usar el inicio de sesión.</noscript>
      </div>
    </section>
  );
}
