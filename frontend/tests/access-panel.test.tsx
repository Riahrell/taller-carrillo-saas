import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AccessPanel from "@/components/access-panel";
import { identityApi } from "@/lib/access/identity";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

const session: SessionContext = {
  user_id: "00000000-0000-4000-8000-000000000001",
  tenant_id: "00000000-0000-4000-8000-000000000002",
  membership_id: "00000000-0000-4000-8000-000000000003",
  tenant_name: "Empresa A",
  role: "admin",
};
const otherId = "00000000-0000-4000-8000-000000000004";
const choices = [
  { membership_id: session.membership_id, tenant_name: "Empresa A" },
  { membership_id: otherId, tenant_name: "Empresa B" },
];
const denied = () => new ApiError("http", "internal-canary", 401);

beforeEach(() => {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  vi.spyOn(accessApi, "session").mockRejectedValue(denied());
  vi.spyOn(accessApi, "prepare").mockResolvedValue(undefined);
  vi.spyOn(accessApi, "login").mockResolvedValue({ status: "authenticated" });
  vi.spyOn(accessApi, "select").mockResolvedValue({ status: "authenticated" });
  vi.spyOn(accessApi, "logout").mockResolvedValue(undefined);
  vi.stubGlobal("BroadcastChannel", undefined);
});
afterEach(() => vi.useRealTimers());

async function ready() {
  render(<AccessPanel />);
  await screen.findByLabelText("Correo electrónico");
}
function submit(password = " sin recortar ") {
  fireEvent.change(screen.getByLabelText("Correo electrónico"), {
    target: { value: "persona@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Contraseña"), {
    target: { value: password },
  });
  fireEvent.submit(
    screen.getByRole("button", { name: "Iniciar sesión" }).closest("form")!,
  );
}

describe("formulario de acceso", () => {
  it("espera al servidor y prepara CSRF antes de habilitar credenciales", async () => {
    render(<AccessPanel />);
    expect(screen.getByRole("status")).toHaveTextContent("Comprobando acceso");
    expect(screen.queryByLabelText("Contraseña")).not.toBeInTheDocument();
    await screen.findByLabelText("Contraseña");
    expect(accessApi.prepare).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Correo electrónico")).toHaveAttribute(
      "autocomplete",
      "username",
    );
    expect(screen.getByLabelText("Contraseña")).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
    expect(screen.getByLabelText("Contraseña")).not.toHaveAttribute(
      "maxlength",
    );
  });

  it("borra la contraseña al enviar, bloquea duplicados y obtiene contexto actual", async () => {
    await ready();
    let finish!: (value: { status: "authenticated" }) => void;
    vi.mocked(accessApi.login).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    vi.mocked(accessApi.session).mockResolvedValue(session);
    submit();
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
    const button = screen.getByRole("button", { name: /Ingresando/ });
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form")!);
    expect(accessApi.login).toHaveBeenCalledTimes(1);
    expect(accessApi.login).toHaveBeenCalledWith(
      "persona@example.com",
      " sin recortar ",
      expect.any(Object),
    );
    await act(async () => finish({ status: "authenticated" }));
    expect(await screen.findByText("Empresa A")).toBeVisible();
    expect(screen.getByText("Administrador")).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Sesión activa" }),
    ).toHaveFocus();
  });

  it("permite mostrar y ocultar contraseña sin alterar su contenido", async () => {
    await ready();
    fireEvent.change(screen.getByLabelText("Contraseña"), {
      target: { value: " temporal " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Mostrar contraseña" }));
    expect(screen.getByLabelText("Contraseña")).toHaveAttribute("type", "text");
    expect(screen.getByLabelText("Contraseña")).toHaveValue(" temporal ");
    fireEvent.click(screen.getByRole("button", { name: "Ocultar contraseña" }));
    expect(screen.getByLabelText("Contraseña")).toHaveAttribute(
      "type",
      "password",
    );
  });

  it("no elige la primera empresa y solo envía una membresía confirmada", async () => {
    await ready();
    vi.mocked(accessApi.login).mockResolvedValue({
      status: "select_membership",
      choices,
    });
    vi.mocked(accessApi.session).mockResolvedValue({
      ...session,
      tenant_name: "Empresa B",
      membership_id: otherId,
      role: "mechanic",
    });
    submit();
    expect(
      await screen.findByRole("heading", { name: "Selecciona una empresa" }),
    ).toHaveFocus();
    expect(screen.getByRole("button", { name: /Continuar/ })).toBeDisabled();
    expect(
      screen
        .getAllByRole("radio")
        .every((input) => !(input as HTMLInputElement).checked),
    ).toBe(true);
    fireEvent.click(screen.getByRole("radio", { name: "Empresa B" }));
    fireEvent.submit(
      screen.getByRole("button", { name: /Continuar/ }).closest("form")!,
    );
    expect(await screen.findByText("Mecánico")).toBeVisible();
    expect(screen.getByText("Empresa B")).toBeVisible();
    expect(accessApi.select).toHaveBeenCalledWith(otherId, expect.any(Object));
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
  });

  it("informa rechazo genérico y no conserva contraseña ni texto del servidor", async () => {
    await ready();
    vi.mocked(accessApi.login).mockRejectedValue(denied());
    submit();
    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("Revisa tus datos o inténtalo más tarde");
    expect(notice).toHaveFocus();
    expect(document.body).not.toHaveTextContent("internal-canary");
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  });

  it.each([403, 429, 503])(
    "ante %s ofrece comprobación sin repetir login",
    async (status) => {
      await ready();
      vi.mocked(accessApi.login).mockRejectedValue(
        new ApiError("http", "sensitive", status),
      );
      submit();
      expect(
        await screen.findByRole("button", { name: "Comprobar acceso" }),
      ).toBeEnabled();
      expect(accessApi.login).toHaveBeenCalledTimes(1);
      expect(screen.queryByLabelText("Contraseña")).not.toBeInTheDocument();
    },
  );

  it("recupera un login de resultado incierto con lectura y sin reenviar contraseña", async () => {
    await ready();
    vi.mocked(accessApi.login).mockRejectedValue(
      new ApiError("network", "sensitive"),
    );
    submit();
    await screen.findByRole("button", { name: "Comprobar acceso" });
    vi.mocked(accessApi.session).mockResolvedValue(session);
    fireEvent.click(screen.getByRole("button", { name: "Comprobar acceso" }));
    expect(await screen.findByText("Empresa A")).toBeVisible();
    expect(accessApi.login).toHaveBeenCalledTimes(1);
  });

  it("retira contexto al cerrar, y no anuncia cierre si falla", async () => {
    vi.mocked(accessApi.session).mockResolvedValue(session);
    render(<AccessPanel />);
    await screen.findByText("Empresa A");
    vi.mocked(accessApi.logout).mockRejectedValue(
      new ApiError("network", "sensitive"),
    );
    fireEvent.click(screen.getByRole("button", { name: /Cerrar sesión/ }));
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Reintentar cierre" }),
    ).toBeEnabled();
    expect(screen.queryByText("Sesión cerrada.")).not.toBeInTheDocument();
    vi.mocked(accessApi.logout).mockResolvedValue(undefined);
    fireEvent.click(screen.getByRole("button", { name: "Reintentar cierre" }));
    expect(await screen.findByText("Sesión cerrada.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  });

  it("cancela selección en servidor y vuelve a credenciales sin opciones anteriores", async () => {
    await ready();
    vi.mocked(accessApi.login).mockResolvedValue({
      status: "select_membership",
      choices,
    });
    submit();
    fireEvent.click(
      await screen.findByRole("button", { name: "Cancelar acceso" }),
    );
    await screen.findByLabelText("Contraseña");
    expect(accessApi.logout).toHaveBeenCalledOnce();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("no trunca Unicode ni aplica al login el mínimo de alta", async () => {
    await ready();
    vi.mocked(accessApi.login).mockRejectedValue(denied());
    submit("🔧".repeat(128));
    await screen.findByRole("alert");
    expect(accessApi.login).toHaveBeenCalledWith(
      "persona@example.com",
      "🔧".repeat(128),
      expect.any(Object),
    );
    submit("x");
    await waitFor(() => expect(accessApi.login).toHaveBeenCalledTimes(2));
    await screen.findByRole("alert");
    submit("🔧".repeat(129));
    expect(await screen.findByRole("alert")).toHaveTextContent("entre 1 y 128");
    expect(accessApi.login).toHaveBeenCalledTimes(2);
  });

  it("revalida al regresar y descarta la empresa anterior cuando vence sesión", async () => {
    vi.mocked(accessApi.session)
      .mockResolvedValueOnce(session)
      .mockRejectedValue(denied());
    render(<AccessPanel />);
    await screen.findByText("Empresa A");
    fireEvent.focus(window);
    await screen.findByLabelText("Contraseña");
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
  });

  it("no persiste credenciales ni contexto en Storage", async () => {
    const read = vi.spyOn(Storage.prototype, "getItem");
    const write = vi.spyOn(Storage.prototype, "setItem");
    await ready();
    vi.mocked(accessApi.session).mockResolvedValue(session);
    submit();
    await screen.findByText("Empresa A");
    expect(read).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });

  it("descarta lecturas que llegan después de desmontar", async () => {
    let finish!: (value: SessionContext) => void;
    vi.mocked(accessApi.session).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const { unmount } = render(<AccessPanel />);
    const signal = vi.mocked(accessApi.session).mock.calls[0][0]?.signal;
    unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => finish(session));
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
  });

  it("retira sesión visible a los 30 minutos sin hacer polling", async () => {
    vi.useFakeTimers();
    vi.mocked(accessApi.session).mockResolvedValue(session);
    render(<AccessPanel />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText("Empresa A")).toBeVisible();
    await act(async () => vi.advanceTimersByTime(30 * 60_000));
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Comprobar acceso" }),
    ).toBeEnabled();
    expect(accessApi.session).toHaveBeenCalledOnce();
  });

  it("una respuesta de sesión anterior no reemplaza la comprobación más reciente", async () => {
    let old!: (value: SessionContext) => void;
    vi.mocked(accessApi.session)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          old = resolve;
        }),
      )
      .mockResolvedValue({ ...session, tenant_name: "Empresa B" });
    render(<AccessPanel />);
    fireEvent.focus(window);
    expect(await screen.findByText("Empresa B")).toBeVisible();
    await act(async () => old(session));
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
    expect(screen.getByText("Empresa B")).toBeVisible();
  });

  it("caduca la selección visible sin enviar escrituras de fondo", async () => {
    await ready();
    vi.useFakeTimers();
    vi.mocked(accessApi.login).mockResolvedValue({
      status: "select_membership",
      choices,
    });
    await act(async () => submit());
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    await act(async () => vi.advanceTimersByTime(5 * 60_000));
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("ha caducado");
    expect(accessApi.select).not.toHaveBeenCalled();
    expect(accessApi.logout).not.toHaveBeenCalled();
  });

  it("una señal entre pestañas solo provoca lectura y no impone empresa ni rol", async () => {
    class Channel {
      onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
      postMessage = vi.fn();
      close = vi.fn();
    }
    const current = new Channel();
    vi.stubGlobal(
      "BroadcastChannel",
      vi.fn(function () {
        return current;
      }),
    );
    vi.mocked(accessApi.session)
      .mockResolvedValueOnce(session)
      .mockRejectedValue(denied());
    const { unmount } = render(<AccessPanel />);
    await screen.findByText("Empresa A");
    await act(async () =>
      current.onmessage?.(new MessageEvent("message", { data: "changed" })),
    );
    await screen.findByLabelText("Contraseña");
    expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
    expect(accessApi.login).not.toHaveBeenCalled();
    expect(accessApi.logout).not.toHaveBeenCalled();
    unmount();
    expect(current.close).toHaveBeenCalledOnce();
  });
});

it("conserva el correo al volver a la pestaña y retira la contraseña", async () => {
  await ready();
  fireEvent.change(screen.getByLabelText("Correo electrónico"), {
    target: { value: "persona@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Contraseña"), {
    target: { value: "temporal" },
  });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  fireEvent.focus(window);
  expect(screen.getByLabelText("Correo electrónico")).toHaveValue(
    "persona@example.com",
  );
  expect(screen.getByLabelText("Contraseña")).toHaveValue("");
  expect(accessApi.session).toHaveBeenCalledTimes(1);
});

it("mantiene el paso de verificación al ir a consultar el correo", async () => {
  await ready();
  vi.mocked(accessApi.login).mockResolvedValue({ status: "mfa_enrollment" });
  vi.spyOn(identityApi, "requestEnrollment").mockResolvedValue(undefined);
  submit();
  await screen.findByLabelText("Código del correo");
  fireEvent.click(
    screen.getByRole("button", { name: "Enviar correo de verificación" }),
  );
  await screen.findByText(/Mensaje solicitado/);
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "hidden",
  });
  fireEvent(document, new Event("visibilitychange"));
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  fireEvent.focus(window);
  expect(screen.getByLabelText("Código del correo")).toBeVisible();
  expect(screen.getByText(/Mensaje solicitado/)).toBeVisible();
  expect(accessApi.login).toHaveBeenCalledTimes(1);
  expect(accessApi.session).toHaveBeenCalledTimes(1);
  expect(identityApi.requestEnrollment).toHaveBeenCalledTimes(1);
});
