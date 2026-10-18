"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { accessApi, type SessionContext } from "@/lib/access/api";
import InventoryManager, {
  uncertainInventory,
  sameInventorySession,
  type ScopedPending,
} from "@/components/inventory-manager";

type View =
  | { kind: "checking" }
  | { kind: "active"; session: SessionContext; pending: ScopedPending | null }
  | { kind: "blocked"; message: string };
export default function InventoryWorkspace() {
  const [view, setView] = useState<View>({ kind: "checking" });
  const controller = useRef<AbortController | null>(null);
  const writing = useRef(false);
  const pending = useRef<ScopedPending | null>(null);
  const remember = useCallback((value: ScopedPending | null) => {
    pending.current = value;
  }, []);
  const channel = useRef<BroadcastChannel | null>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const problem = useCallback((message: string) => {
    controller.current?.abort();
    setView({ kind: "blocked", message });
  }, []);
  const check = useCallback(() => {
    controller.current?.abort();
    if (writing.current) {
      problem(uncertainInventory);
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setView({ kind: "checking" });
    void accessApi
      .session({ signal: current.signal })
      .then((session) => {
        if (!current.signal.aborted)
          setView(
            session.role === "admin" &&
              (!pending.current ||
                sameInventorySession(session, pending.current.session))
              ? { kind: "active", session, pending: pending.current }
              : {
                  kind: "blocked",
                  message: pending.current
                    ? "Hay un conteo pendiente de resolver en la sesión original. Vuelve al inicio para recuperar el acceso de esa empresa."
                    : "Tu perfil no permite consultar el inventario.",
                },
          );
      })
      .catch(() => {
        if (!current.signal.aborted)
          problem(
            "No pudimos comprobar tu sesión. Vuelve al inicio o comprueba el acceso para continuar.",
          );
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
    check();
    const focus = () => {
      if (document.visibilityState === "visible") check();
    };
    const visibility = () => {
      if (document.visibilityState === "hidden")
        problem(
          writing.current
            ? uncertainInventory
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
      channel.current?.close();
      window.removeEventListener("focus", focus);
      window.removeEventListener("pageshow", focus);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [check, problem]);
  useEffect(() => {
    if (view.kind === "blocked") notice.current?.focus();
  }, [view]);
  return (
    <main id="contenido" className="memberships-workspace" tabIndex={-1}>
      <nav aria-label="Navegación">
        <Link href="/" className="text-action">
          ← Volver al inicio
        </Link>
      </nav>
      <p className="eyebrow">EXISTENCIAS Y VALORACIÓN</p>
      <h1>Inventario</h1>
      {view.kind === "checking" && <p role="status">Comprobando acceso…</p>}
      {view.kind === "blocked" && (
        <section className="membership-notice">
          <p role="alert" tabIndex={-1} ref={notice}>
            {view.message}
          </p>
          <button className="action" onClick={check}>
            Comprobar acceso
          </button>
        </section>
      )}
      {view.kind === "active" && (
        <>
          <p className="membership-company">
            {view.session.tenant_name} <span>· Administrador</span>
          </p>
          <InventoryManager
            key={view.session.membership_id}
            session={view.session}
            onProblem={problem}
            onWriting={onWriting}
            onChanged={announce}
            initialPending={view.pending}
            onPending={remember}
          />
        </>
      )}
    </main>
  );
}
