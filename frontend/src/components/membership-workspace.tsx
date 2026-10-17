"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import MembershipManager from "@/components/membership-manager";
import MembershipCreation from "@/components/membership-creation";
import IdentityConfirmation from "@/components/identity-confirmation";

type View =
  | { kind: "checking" }
  | { kind: "confirm"; session: SessionContext }
  | { kind: "active"; session: SessionContext; remaining: number }
  | { kind: "blocked"; message: string };
export const uncertainChange =
  "No pudimos confirmar el resultado del cambio. Comprueba el acceso y revisa la membresía antes de volver a modificarla.";
async function load(signal: AbortSignal): Promise<View> {
  try {
    const session = await accessApi.session({ signal });
    return session.role === "admin"
      ? { kind: "confirm", session }
      : {
          kind: "blocked",
          message: "Tu perfil no permite administrar membresías.",
        };
  } catch (error) {
    return {
      kind: "blocked",
      message:
        error instanceof ApiError && error.status === 401
          ? "Tu sesión ya no está disponible. Vuelve al inicio para acceder."
          : "No pudimos comprobar tu acceso. Inténtalo de nuevo cuando el servicio esté disponible.",
    };
  }
}
export default function MembershipWorkspace({
  mode = "manage",
}: {
  mode?: "manage" | "create";
}) {
  const [view, setView] = useState<View>({ kind: "checking" });
  const controller = useRef<AbortController | null>(null);
  const writing = useRef(false);
  const channel = useRef<BroadcastChannel | null>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const problem = useCallback((message: string) => {
    controller.current?.abort();
    setView({ kind: "blocked", message });
  }, []);
  const check = useCallback(() => {
    controller.current?.abort();
    if (writing.current) {
      problem(uncertainChange);
      return;
    }
    setView({ kind: "checking" });
    const current = new AbortController();
    controller.current = current;
    void load(current.signal).then((next) => {
      if (!current.signal.aborted) setView(next);
    });
  }, [problem]);
  const onWriting = useCallback((value: boolean) => {
    writing.current = value;
  }, []);
  const announce = useCallback(
    () => channel.current?.postMessage("changed"),
    [],
  );
  useEffect(() => {
    const current = new AbortController();
    controller.current = current;
    void load(current.signal).then((next) => {
      if (!current.signal.aborted) setView(next);
    });
    const focus = () => {
      if (document.visibilityState === "visible") check();
    };
    const visibility = () => {
      if (document.visibilityState === "hidden")
        problem(
          writing.current
            ? uncertainChange
            : "Comprueba el acceso al volver para continuar.",
        );
      else check();
    };
    window.addEventListener("focus", focus);
    window.addEventListener("pageshow", focus);
    document.addEventListener("visibilitychange", visibility);
    if (typeof BroadcastChannel !== "undefined") {
      channel.current = new BroadcastChannel("carrillo_access");
      channel.current.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data === "changed") check();
      };
    }
    return () => {
      controller.current?.abort();
      window.removeEventListener("focus", focus);
      window.removeEventListener("pageshow", focus);
      document.removeEventListener("visibilitychange", visibility);
      channel.current?.close();
    };
  }, [check, problem]);
  useEffect(() => {
    if (view.kind === "blocked") notice.current?.focus();
  }, [view]);
  useEffect(() => {
    if (view.kind !== "active") return;
    const timer = setTimeout(
      () =>
        problem(
          writing.current
            ? uncertainChange
            : "Vuelve a comprobar tu acceso para continuar.",
        ),
      view.remaining,
    );
    return () => clearTimeout(timer);
  }, [view, problem]);
  return (
    <main id="contenido" className="memberships-workspace" tabIndex={-1}>
      <nav aria-label="Navegación">
        <Link
          href={mode === "create" ? "/membresias" : "/"}
          className="text-action"
        >
          {mode === "create" ? "← Volver a membresías" : "← Volver al inicio"}
        </Link>
      </nav>
      <p className="eyebrow">PERSONAL Y ACCESOS</p>
      <h1>{mode === "create" ? "Nuevo acceso" : "Membresías"}</h1>
      {view.kind === "checking" && <p role="status">Comprobando acceso…</p>}
      {view.kind === "blocked" && (
        <section className="membership-notice">
          <p role="alert" ref={notice} tabIndex={-1}>
            {view.message}
          </p>
          <button className="action" onClick={check}>
            Comprobar acceso
          </button>
        </section>
      )}
      {(view.kind === "active" || view.kind === "confirm") && (
        <>
          <p className="membership-company">
            {view.session.tenant_name} <span>· Administrador</span>
          </p>
          {view.kind === "confirm" ? (
            <IdentityConfirmation
              session={view.session}
              onWriting={onWriting}
              onChanged={announce}
              onProblem={problem}
              onConfirmed={(remaining) =>
                setView({ kind: "active", session: view.session, remaining })
              }
            />
          ) : mode === "create" ? (
            <MembershipCreation
              key={`${view.session.user_id}:${view.session.tenant_id}:${view.session.membership_id}`}
              session={view.session}
              onProblem={problem}
              onWriting={onWriting}
              onChanged={announce}
            />
          ) : (
            <MembershipManager
              key={`${view.session.user_id}:${view.session.tenant_id}:${view.session.membership_id}`}
              session={view.session}
              onProblem={problem}
              onWriting={onWriting}
              onChanged={announce}
              onRecheck={check}
            />
          )}
        </>
      )}
    </main>
  );
}
