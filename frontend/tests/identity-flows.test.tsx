import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MfaFlow from "@/components/mfa-flow";
import PasswordRecovery from "@/components/password-recovery";
import { identityApi } from "@/lib/access/identity";

beforeEach(() => {
  history.replaceState(null, "", "/");
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
});
afterEach(() => history.replaceState(null, "", "/"));

function hide() {
  act(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

it("strips reset fragments without consuming them until explicit submission", async () => {
  const token = "a".repeat(43);
  history.replaceState(null, "", `/recuperar#reset=${token}`);
  const reset = vi.spyOn(identityApi, "reset").mockResolvedValue(undefined);
  render(<PasswordRecovery />);
  expect(location.hash).toBe("");
  expect(reset).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Contraseña nueva"), {
    target: { value: "Synthetic test phrase 92!" },
  });
  fireEvent.change(screen.getByLabelText("Repite la contraseña"), {
    target: { value: "Synthetic test phrase 92!" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Cambiar contraseña" }));
  await waitFor(() =>
    expect(reset).toHaveBeenCalledWith(token, "Synthetic test phrase 92!"),
  );
  expect(sessionStorage.length).toBe(0);
  expect(localStorage.length).toBe(0);
});

it("removes the retained reset token and entered password when hidden", () => {
  history.replaceState(null, "", `/recuperar#reset=${"b".repeat(43)}`);
  render(<PasswordRecovery />);
  fireEvent.change(screen.getByLabelText("Contraseña nueva"), {
    target: { value: "Synthetic test phrase 92!" },
  });
  hide();
  expect(screen.getByLabelText("Contraseña nueva")).toHaveValue("");
  expect(screen.getByLabelText("Código del mensaje")).toHaveValue("");
});

it("does not redisplay a setup secret returned after the page was hidden", async () => {
  let resolve!: (
    value: Awaited<ReturnType<typeof identityApi.confirmEnrollment>>,
  ) => void;
  vi.spyOn(identityApi, "confirmEnrollment").mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  render(
    <MfaFlow initial="mfa_enrollment" onLogin={vi.fn()} onCancel={vi.fn()} />,
  );
  fireEvent.change(screen.getByLabelText("Código del correo"), {
    target: { value: "c".repeat(43) },
  });
  fireEvent.click(screen.getByRole("button", { name: "Confirmar" }));
  hide();
  await act(async () =>
    resolve({
      status: "confirm_factor",
      seed: "synthetic-seed-canary",
      qr: "data:image/svg+xml;base64,PHN2Zy8+",
    }),
  );
  expect(
    screen.queryByLabelText("Clave de configuración"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("synthetic-seed-canary")).not.toBeInTheDocument();
  expect(screen.getByText("Reinicia el acceso")).toBeInTheDocument();
});
