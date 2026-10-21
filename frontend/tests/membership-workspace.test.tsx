import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MembershipWorkspace from "@/components/membership-workspace";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import { membershipsApi, type Page, type Detail } from "@/lib/memberships/api";
import { confirmIdentity } from "./confirm-identity";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const etag = `"membership-v1-${"a".repeat(64)}"`;
const context: SessionContext = {
  user_id: actor,
  membership_id: actor,
  tenant_id: target,
  tenant_name: "Empresa A",
  role: "admin",
};
const member = {
  membership_id: target,
  role: "cashier" as const,
  is_active: true,
  email: "persona@example.com",
  user_is_active: true,
};
const page: Page = { items: [member], next_after: null };
const detail: Detail = {
  membership_id: target,
  role: "cashier",
  is_active: true,
  etag,
};
beforeEach(() => {
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(accessApi, "reauthenticate").mockResolvedValue(undefined);
  vi.spyOn(membershipsApi, "page").mockResolvedValue(page);
  vi.spyOn(membershipsApi, "detail").mockResolvedValue(detail);
  vi.spyOn(membershipsApi, "change").mockResolvedValue({
    ...detail,
    role: "mechanic",
  });
});
afterEach(() => vi.useRealTimers());
async function open() {
  render(<MembershipWorkspace />);
  await confirmIdentity();
  await screen.findByText(member.email);
}
async function edit() {
  fireEvent.click(
    screen.getByRole("button", { name: `Revisar acceso de ${member.email}` }),
  );
  await screen.findByLabelText("Perfil");
}
function propose() {
  fireEvent.change(screen.getByLabelText("Perfil"), {
    target: { value: "mechanic" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar cambio" }));
}
describe("administración de membresías", () => {
  it("espera confirmación antes de consultar o montar controles administrativos", async () => {
    render(<MembershipWorkspace />);
    await screen.findByLabelText("Tu contraseña actual");
    expect(membershipsApi.page).not.toHaveBeenCalled();
    expect(membershipsApi.detail).not.toHaveBeenCalled();
    expect(membershipsApi.change).not.toHaveBeenCalled();
    await confirmIdentity();
    await screen.findByText(member.email);
  });
  it("perder contexto mientras confirma descarta la respuesta tardía", async () => {
    let finish!: () => void;
    vi.mocked(accessApi.reauthenticate).mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    render(<MembershipWorkspace />);
    await confirmIdentity();
    fireEvent.focus(window);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No pudimos confirmar el resultado",
    );
    await act(async () => finish());
    expect(membershipsApi.page).not.toHaveBeenCalled();
    expect(accessApi.reauthenticate).toHaveBeenCalledOnce();
    expect(
      screen.queryByLabelText("Tu contraseña actual"),
    ).not.toBeInTheDocument();
  });
  it("428 retira el borrador y exige nueva confirmación sin reenviar PATCH", async () => {
    vi.mocked(membershipsApi.change).mockRejectedValue(
      new ApiError("http", "private", 428),
    );
    await open();
    await edit();
    propose();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "confirmación de identidad venció",
    );
    expect(screen.queryByLabelText("Perfil")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Comprobar acceso" }));
    await screen.findByLabelText("Tu contraseña actual");
    expect(membershipsApi.change).toHaveBeenCalledOnce();
    expect(accessApi.reauthenticate).toHaveBeenCalledOnce();
  });
  it("al perder el foco durante un guardado retira datos y descarta el resultado tardío", async () => {
    let finish!: (value: Detail) => void;
    vi.mocked(membershipsApi.change).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    await open();
    await edit();
    propose();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
    fireEvent.focus(window);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No pudimos confirmar el resultado",
    );
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();
    await act(async () => finish({ ...detail, role: "mechanic" }));
    expect(
      screen.queryByText("Cambio confirmado. El listado se ha actualizado."),
    ).not.toBeInTheDocument();
    expect(membershipsApi.change).toHaveBeenCalledOnce();
  });
  it("las señales de otra pestaña solo provocan una nueva autorización", async () => {
    class Channel {
      onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
      close = vi.fn();
      postMessage = vi.fn();
    }
    const current = new Channel();
    vi.stubGlobal(
      "BroadcastChannel",
      vi.fn(function () {
        return current;
      }),
    );
    await open();
    await act(async () =>
      current.onmessage?.(
        new MessageEvent("message", { data: { role: "admin" } }),
      ),
    );
    expect(accessApi.session).toHaveBeenCalledTimes(2);
    vi.mocked(accessApi.session).mockResolvedValue({
      ...context,
      role: "read_only",
    });
    await act(async () =>
      current.onmessage?.(new MessageEvent("message", { data: "changed" })),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "no permite administrar",
    );
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();
    expect(membershipsApi.change).not.toHaveBeenCalled();
  });
  it.each(["cashier", "mechanic", "read_only"] as const)(
    "no consulta datos cuando el servidor devuelve perfil %s",
    async (role) => {
      vi.mocked(accessApi.session).mockResolvedValue({ ...context, role });
      render(<MembershipWorkspace />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "no permite administrar",
      );
      expect(membershipsApi.page).not.toHaveBeenCalled();
    },
  );
  it("sesión inválida o pendiente no muestra datos", async () => {
    vi.mocked(accessApi.session).mockRejectedValue(
      new ApiError("http", "private", 401),
    );
    render(<MembershipWorkspace />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "sesión ya no está disponible",
    );
    expect(membershipsApi.page).not.toHaveBeenCalled();
  });
  it("lista estados y revisa detalle actual sin preseleccionar un cambio", async () => {
    await open();
    await edit();
    expect(membershipsApi.detail).toHaveBeenCalledWith(
      actor,
      target,
      expect.any(Object),
    );
    expect(screen.getByLabelText("Perfil")).toHaveValue("cashier");
    expect(
      screen.getByRole("button", { name: "Revisar cambio" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Confirmar cambio" }),
    ).not.toBeInTheDocument();
    expect(membershipsApi.change).not.toHaveBeenCalled();
  });
  it("exige revisión/confirmación y solo informa éxito tras refrescar estado", async () => {
    await open();
    await edit();
    propose();
    expect(membershipsApi.change).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
    expect(
      await screen.findByText(
        "Cambio confirmado. El listado se ha actualizado.",
      ),
    ).toHaveAttribute("role", "status");
    expect(membershipsApi.change).toHaveBeenCalledWith(
      actor,
      target,
      { role: "mechanic", is_active: true },
      etag,
      expect.any(Object),
    );
    expect(membershipsApi.page).toHaveBeenCalledTimes(2);
  });
  it("bloquea doble confirmación y cancelación durante la escritura", async () => {
    let finish!: (value: Detail) => void;
    vi.mocked(membershipsApi.change).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    await open();
    await edit();
    propose();
    const button = screen.getByRole("button", { name: "Confirmar cambio" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(button).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Cancelar revisión" }),
    ).toBeDisabled();
    expect(membershipsApi.change).toHaveBeenCalledOnce();
    await act(async () => finish({ ...detail, role: "mechanic" }));
    await screen.findByText("Cambio confirmado. El listado se ha actualizado.");
  });
  it("cancela o vuelve a editar sin escribir", async () => {
    await open();
    await edit();
    propose();
    fireEvent.click(screen.getByRole("button", { name: "Volver a editar" }));
    expect(screen.getByLabelText("Perfil")).toHaveValue("mechanic");
    fireEvent.click(screen.getByRole("button", { name: "Cancelar revisión" }));
    await screen.findByRole("heading", { name: "Accesos de la empresa" });
    expect(membershipsApi.change).not.toHaveBeenCalled();
  });
  it.each([409, 412])(
    "ante %s exige consultar y revisar de nuevo",
    async (status) => {
      vi.mocked(membershipsApi.change).mockRejectedValue(
        new ApiError("http", "private-canary", status),
      );
      await open();
      await edit();
      propose();
      fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
      const alert = await screen.findByRole("alert");
      await waitFor(() => expect(alert).toHaveFocus());
      expect(document.body).not.toHaveTextContent("private-canary");
      expect(
        screen.queryByRole("button", { name: "Confirmar cambio" }),
      ).not.toBeInTheDocument();
      vi.mocked(membershipsApi.detail).mockResolvedValue({
        ...detail,
        role: "read_only",
        etag: `"membership-v1-${"b".repeat(64)}"`,
      });
      fireEvent.click(
        screen.getByRole("button", { name: "Revisar estado actual" }),
      );
      expect(await screen.findByLabelText("Perfil")).toHaveValue("read_only");
      expect(membershipsApi.change).toHaveBeenCalledOnce();
    },
  );
  it.each([401, 403, 503, null])(
    "retira datos y no repite escritura de resultado incierto: %s",
    async (status) => {
      vi.mocked(membershipsApi.change).mockRejectedValue(
        new ApiError(status ? "http" : "network", "private", status),
      );
      await open();
      await edit();
      propose();
      fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "No pudimos confirmar el resultado",
      );
      expect(screen.queryByText(member.email)).not.toBeInTheDocument();
      expect(screen.queryByText("Empresa A")).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: "Comprobar acceso" }));
      await confirmIdentity();
      await screen.findByText(member.email);
      expect(membershipsApi.change).toHaveBeenCalledOnce();
    },
  );
  it("pagina sin acumular datos ni conservar la página anterior", async () => {
    vi.mocked(membershipsApi.page)
      .mockResolvedValueOnce({ ...page, next_after: target })
      .mockResolvedValue({ items: [], next_after: null });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "Página siguiente" }));
    expect(
      await screen.findByText("No hay accesos en esta página."),
    ).toBeVisible();
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();
    expect(membershipsApi.page).toHaveBeenLastCalledWith(
      actor,
      target,
      expect.any(Object),
    );
  });
  it("retira contexto al volver a foco y descarta una lectura anterior", async () => {
    let finish!: (value: Page) => void;
    vi.mocked(membershipsApi.page).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    render(<MembershipWorkspace />);
    await confirmIdentity();
    await waitFor(() => expect(membershipsApi.page).toHaveBeenCalledOnce());
    vi.mocked(accessApi.session).mockRejectedValue(
      new ApiError("http", "private", 401),
    );
    fireEvent.focus(window);
    await screen.findByRole("alert");
    await act(async () => finish(page));
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();
  });
  it("recomprueba sesión tras modificar el propio acceso", async () => {
    vi.mocked(membershipsApi.page).mockResolvedValue({
      items: [{ ...member, membership_id: actor, role: "admin" }],
      next_after: null,
    });
    vi.mocked(membershipsApi.detail).mockResolvedValue({
      ...detail,
      membership_id: actor,
      role: "admin",
    });
    await open();
    await edit();
    propose();
    expect(screen.getByText("Este cambio cerrará tu sesión.")).toBeVisible();
    vi.mocked(accessApi.session).mockRejectedValue(
      new ApiError("http", "private", 401),
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirmar cambio" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "sesión ya no está disponible",
    );
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();
  });
  it("retira datos tras inactividad sin polling ni Storage", async () => {
    const read = vi.spyOn(Storage.prototype, "getItem"),
      write = vi.spyOn(Storage.prototype, "setItem");
    render(<MembershipWorkspace />);
    await screen.findByLabelText("Tu contraseña actual");
    vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText("Tu contraseña actual"), {
      target: { value: "synthetic current password" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirmar identidad" }),
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTime(200_000));
    fireEvent.focus(window);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText(member.email)).toBeVisible();
    await act(async () => vi.advanceTimersByTime(70_000));
    expect(screen.queryByText(member.email)).not.toBeInTheDocument();
    expect(read).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
    expect(accessApi.session).toHaveBeenCalledTimes(3);
  });
});
