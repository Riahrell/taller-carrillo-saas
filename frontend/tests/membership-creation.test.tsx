import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MembershipWorkspace from "@/components/membership-workspace";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
import { membershipsApi, type Detail } from "@/lib/memberships/api";

const actor = "00000000-0000-4000-8000-000000000001";
const target = "00000000-0000-4000-8000-000000000002";
const context: SessionContext = {
  user_id: actor,
  membership_id: actor,
  tenant_id: target,
  tenant_name: "Empresa A",
  role: "admin",
};
const created: Detail = {
  membership_id: target,
  role: "mechanic",
  is_active: true,
  etag: `"membership-v1-${"a".repeat(64)}"`,
};
const email = "new@example.com";
const password = " " + "🔧".repeat(15) + " ";
beforeEach(() => {
  vi.stubGlobal("BroadcastChannel", undefined);
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(membershipsApi, "create").mockResolvedValue(created);
  vi.spyOn(membershipsApi, "detail").mockResolvedValue(created);
  vi.spyOn(membershipsApi, "page").mockResolvedValue({
    items: [],
    next_after: null,
  });
});
async function open() {
  render(<MembershipWorkspace mode="create" />);
  await screen.findByLabelText("Correo del nuevo usuario");
}
async function review() {
  await open();
  fireEvent.change(screen.getByLabelText("Correo del nuevo usuario"), {
    target: { value: email },
  });
  fireEvent.change(screen.getByLabelText("Perfil del nuevo usuario"), {
    target: { value: "mechanic" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revisar alta" }));
  await screen.findByLabelText("Contraseña del nuevo usuario");
}
function fill(value = password, confirmation = value) {
  fireEvent.change(screen.getByLabelText("Contraseña del nuevo usuario"), {
    target: { value },
  });
  fireEvent.change(screen.getByLabelText("Repetir contraseña"), {
    target: { value: confirmation },
  });
}
function confirm() {
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar y crear acceso" }),
  );
}
describe("alta asistida", () => {
  it.each(["cashier", "mechanic", "read_only"] as const)(
    "no presenta formulario a %s",
    async (role) => {
      vi.mocked(accessApi.session).mockResolvedValue({ ...context, role });
      render(<MembershipWorkspace mode="create" />);
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Tu perfil no permite",
      );
      expect(
        screen.queryByLabelText("Correo del nuevo usuario"),
      ).not.toBeInTheDocument();
      expect(membershipsApi.create).not.toHaveBeenCalled();
    },
  );
  it("no elige perfil ni solicita contraseña antes de revisar", async () => {
    await open();
    expect(screen.getByLabelText("Perfil del nuevo usuario")).toHaveValue("");
    expect(
      screen.queryByLabelText("Contraseña del nuevo usuario"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revisar alta" }));
    expect(membershipsApi.create).not.toHaveBeenCalled();
  });
  it("solo confirma con credenciales coincidentes y las retira antes de esperar red", async () => {
    let finish!: (result: Detail) => void;
    let sent = "";
    vi.mocked(membershipsApi.create).mockImplementation((_actor, data) => {
      sent = data.password;
      return new Promise((resolve) => {
        finish = resolve;
      });
    });
    await review();
    expect(screen.getByText(email)).toBeInTheDocument();
    fill();
    confirm();
    confirm();
    expect(sent).toBe(password);
    expect(screen.getByLabelText("Contraseña del nuevo usuario")).toHaveValue(
      "",
    );
    expect(screen.getByLabelText("Repetir contraseña")).toHaveValue("");
    expect(
      screen.getByRole("button", { name: "Confirmar y crear acceso" }),
    ).toBeDisabled();
    expect(
      screen.queryByRole("link", { name: "Cancelar alta" }),
    ).not.toBeInTheDocument();
    await act(async () => finish(created));
    expect(
      await screen.findByRole("heading", { name: "Acceso creado" }),
    ).toBeInTheDocument();
    expect(membershipsApi.create).toHaveBeenCalledOnce();
    expect(accessApi.session).toHaveBeenCalledTimes(2);
    expect(membershipsApi.detail).toHaveBeenCalledWith(
      actor,
      target,
      expect.any(Object),
    );
  });
  it.each([
    ["x".repeat(14), "x".repeat(14)],
    ["x".repeat(129), "x".repeat(129)],
    [password, password + "x"],
  ])(
    "valida Unicode/límites y coincidencia sin conservar claves",
    async (value, repeat) => {
      await review();
      fill(value, repeat);
      confirm();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Las contraseñas deben coincidir",
      );
      await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
      expect(screen.getByLabelText("Contraseña del nuevo usuario")).toHaveValue(
        "",
      );
      expect(membershipsApi.create).not.toHaveBeenCalled();
    },
  );
  it("volver a editar y cancelar retiran credenciales sin escribir", async () => {
    await review();
    fill();
    const old = screen.getByLabelText("Contraseña del nuevo usuario");
    fireEvent.click(screen.getByRole("button", { name: "Volver a editar" }));
    expect(old).toHaveValue("");
    expect(
      screen.queryByLabelText("Contraseña del nuevo usuario"),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Correo del nuevo usuario")).toHaveValue(
      email,
    );
    expect(membershipsApi.create).not.toHaveBeenCalled();
  });
  it.each([409, 422])(
    "tras %s exige revisar otra vez y no repite POST",
    async (status) => {
      vi.mocked(membershipsApi.create).mockRejectedValue(
        new ApiError("http", "private", status),
      );
      await review();
      fill();
      confirm();
      expect(await screen.findByRole("alert")).not.toHaveTextContent("private");
      expect(screen.getByLabelText("Correo del nuevo usuario")).toHaveValue(
        email,
      );
      expect(
        screen.queryByLabelText("Contraseña del nuevo usuario"),
      ).not.toBeInTheDocument();
      expect(membershipsApi.create).toHaveBeenCalledOnce();
      fireEvent.click(screen.getByRole("button", { name: "Revisar alta" }));
      expect(screen.getByLabelText("Contraseña del nuevo usuario")).toHaveValue(
        "",
      );
    },
  );
  it.each([401, 403, 428, 429, 503, null])(
    "respuesta incierta %s retira el formulario sin repetir alta",
    async (status) => {
      vi.mocked(membershipsApi.create).mockRejectedValue(
        new ApiError(status ? "http" : "network", "private", status),
      );
      await review();
      fill();
      confirm();
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "No pudimos confirmar el resultado del alta",
      );
      expect(screen.queryByText(email)).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Contraseña del nuevo usuario"),
      ).not.toBeInTheDocument();
      expect(membershipsApi.create).toHaveBeenCalledOnce();
    },
  );
  it("si cambia contexto después del POST no muestra éxito", async () => {
    vi.mocked(accessApi.session)
      .mockResolvedValueOnce(context)
      .mockResolvedValue({ ...context, membership_id: target });
    await review();
    fill();
    confirm();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No pudimos confirmar",
    );
    expect(
      screen.queryByRole("heading", { name: "Acceso creado" }),
    ).not.toBeInTheDocument();
  });
  it("ocultar la pestaña elimina la contraseña del nodo anterior", async () => {
    await review();
    fill();
    const old = screen.getByLabelText("Contraseña del nuevo usuario");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    fireEvent(document, new Event("visibilitychange"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Comprueba el acceso",
    );
    expect(old).toHaveValue("");
    expect(membershipsApi.create).not.toHaveBeenCalled();
  });
  it("un guardado interrumpido descarta respuesta tardía sin repetir", async () => {
    let finish!: (result: Detail) => void;
    vi.mocked(membershipsApi.create).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    await review();
    fill();
    confirm();
    fireEvent.focus(window);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No pudimos confirmar el resultado del cambio",
    );
    await act(async () => finish(created));
    expect(
      screen.queryByRole("heading", { name: "Acceso creado" }),
    ).not.toBeInTheDocument();
    expect(membershipsApi.create).toHaveBeenCalledOnce();
  });
});
