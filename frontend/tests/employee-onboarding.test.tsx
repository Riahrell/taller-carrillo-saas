import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import EmployeeRegistration from "@/components/employee-registration";
import EmployeeManager from "@/components/employee-manager";
import MembershipWorkspace from "@/components/membership-workspace";
import { employeesApi, registrationApi } from "@/lib/employees/api";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";
const actor = "00000000-0000-4000-8000-000000000001",
  target = "00000000-0000-4000-8000-000000000002";
const context: SessionContext = {
  user_id: actor,
  membership_id: actor,
  tenant_id: target,
  tenant_name: "Empresa A",
  role: "admin",
};
const code = "a".repeat(43),
  address = "empleado@example.com",
  password = "Una frase privada de prueba 2026";
const invite = {
  id: target,
  email: address,
  created_at: "2026-10-21T00:00:00Z",
  expires_at: "2026-10-22T00:00:00Z",
  status: "pending" as const,
};
const props = {
  session: context,
  onProblem: vi.fn(),
  onWriting: vi.fn(),
  onChanged: vi.fn(),
};
beforeEach(() => {
  history.replaceState(null, "", "/registro#invitacion=" + code);
  vi.spyOn(registrationApi, "prepare").mockResolvedValue(undefined);
  vi.spyOn(registrationApi, "status").mockRejectedValue(
    new ApiError("http", "Unavailable", 400),
  );
  vi.spyOn(registrationApi, "start").mockResolvedValue("awaiting_code");
  vi.spyOn(registrationApi, "confirm").mockResolvedValue("pending_approval");
  vi.spyOn(registrationApi, "resend").mockResolvedValue(undefined);
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
  vi.spyOn(employeesApi, "invitations").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(employeesApi, "pending").mockResolvedValue({
    items: [],
    next_after: null,
  });
  vi.spyOn(employeesApi, "invite").mockResolvedValue({
    invitation: invite,
    code,
  });
  vi.spyOn(employeesApi, "approve").mockResolvedValue(target);
});
describe("registro por invitación", () => {
  it("soporta StrictMode, elimina el fragmento y verifica sin crear una sesión", async () => {
    render(
      <StrictMode>
        <EmployeeRegistration />
      </StrictMode>,
    );
    await screen.findByLabelText("Tu correo electrónico");
    expect(location.hash).toBe("");
    fireEvent.change(screen.getByLabelText("Tu correo electrónico"), {
      target: { value: address },
    });
    for (const label of ["Crea tu contraseña", "Repite tu contraseña"])
      fireEvent.change(screen.getByLabelText(label), {
        target: { value: password },
      });
    fireEvent.click(
      screen.getByRole("button", { name: "Crear cuenta y verificar correo" }),
    );
    await screen.findByLabelText("Código del correo");
    expect(registrationApi.start).toHaveBeenCalledWith(
      { email: address, password, invitation: code },
      expect.any(Object),
    );
    expect(
      screen.queryByLabelText("Crea tu contraseña"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Reenviar en 60 s" }),
    ).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Código del correo"), {
      target: { value: "abc234" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Verificar mi correo" }),
    );
    await screen.findByRole("heading", { name: "Correo verificado" });
    expect(registrationApi.confirm).toHaveBeenCalledWith(
      "ABC234",
      expect.any(Object),
    );
    expect(accessApi.session).not.toHaveBeenCalled();
  });
  it("sin invitación no ofrece registro libre ni selector de empresa", async () => {
    history.replaceState(null, "", "/registro");
    render(<EmployeeRegistration />);
    await screen.findByText(/Abre la invitación privada/);
    expect(
      screen.queryByLabelText("Tu correo electrónico"),
    ).not.toBeInTheDocument();
    expect(registrationApi.start).not.toHaveBeenCalled();
  });
  it("recupera el recibo pendiente sin volver a registrar", async () => {
    history.replaceState(null, "", "/registro");
    vi.mocked(registrationApi.status).mockResolvedValue("pending_approval");
    render(<EmployeeRegistration />);
    await screen.findByRole("heading", { name: "Correo verificado" });
    expect(registrationApi.start).not.toHaveBeenCalled();
  });
});
describe("administración de incorporación", () => {
  it("exige revisión y oculta el enlace al salir de pestaña", async () => {
    render(<EmployeeManager {...props} />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Revisar invitación" }),
      ).toBeEnabled(),
    );
    fireEvent.change(screen.getByLabelText("Correo del empleado"), {
      target: { value: address },
    });
    fireEvent.click(screen.getByRole("button", { name: "Revisar invitación" }));
    expect(employeesApi.invite).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Crear invitación" }));
    await screen.findByLabelText("Enlace privado de registro");
    expect(screen.getByLabelText("Enlace privado de registro")).toHaveValue(
      location.origin + "/registro#invitacion=" + code,
    );
    const visibility = vi
      .spyOn(document, "visibilityState", "get")
      .mockReturnValue("hidden");
    await act(async () =>
      document.dispatchEvent(new Event("visibilitychange")),
    );
    expect(
      screen.queryByLabelText("Enlace privado de registro"),
    ).not.toBeInTheDocument();
    visibility.mockRestore();
  });
  it("aprobar exige confirmación y no envía rol ni empresa", async () => {
    vi.mocked(employeesApi.pending)
      .mockResolvedValueOnce({
        items: [{ id: target, email: address, verified_at: invite.created_at }],
        next_after: null,
      })
      .mockResolvedValue({ items: [], next_after: null });
    render(<EmployeeManager {...props} />);
    fireEvent.click(
      await screen.findByRole("button", {
        name: `Revisar incorporación de ${address}`,
      }),
    );
    expect(employeesApi.approve).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", { name: "Aprobar incorporación" }),
    );
    await screen.findByText(/Incorporación aprobada sin permisos/);
    expect(employeesApi.approve).toHaveBeenCalledWith(
      actor,
      target,
      expect.any(Object),
    );
  });
  it("un perfil sin autoridad no monta administración", async () => {
    vi.mocked(accessApi.session).mockResolvedValue({
      ...context,
      role: "read_only",
    });
    render(<MembershipWorkspace mode="employees" />);
    await screen.findByText("Tu perfil no permite administrar membresías.");
    expect(employeesApi.invitations).not.toHaveBeenCalled();
    expect(employeesApi.pending).not.toHaveBeenCalled();
  });
});
