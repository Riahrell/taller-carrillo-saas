import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import IdentityConfirmation from "@/components/identity-confirmation";
import { accessApi, type SessionContext } from "@/lib/access/api";
import { ApiError } from "@/lib/api/client";

const context: SessionContext = {
  user_id: "00000000-0000-4000-8000-000000000001",
  membership_id: "00000000-0000-4000-8000-000000000002",
  tenant_id: "00000000-0000-4000-8000-000000000003",
  tenant_name: "Empresa sintética",
  role: "admin",
};
beforeEach(() => {
  vi.spyOn(accessApi, "reauthenticate").mockResolvedValue(undefined);
  vi.spyOn(accessApi, "session").mockResolvedValue(context);
});
function open() {
  const callbacks = {
    onWriting: vi.fn(),
    onChanged: vi.fn(),
    onProblem: vi.fn(),
    onConfirmed: vi.fn(),
  };
  const view = render(
    <IdentityConfirmation session={context} {...callbacks} />,
  );
  const input = screen.getByLabelText(
    "Tu contraseña actual",
  ) as HTMLInputElement;
  expect(input).toHaveFocus();
  fireEvent.change(input, {
    target: { value: " synthetic current password " },
  });
  const form = input.closest("form")!;
  return { ...callbacks, ...view, input, form };
}
describe("confirmación de identidad", () => {
  it("limpia el secreto antes de esperar, impide doble envío y relee contexto", async () => {
    let finish!: () => void;
    vi.mocked(accessApi.reauthenticate).mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    const view = open();
    const read = vi.spyOn(Storage.prototype, "getItem"),
      write = vi.spyOn(Storage.prototype, "setItem");
    fireEvent.submit(view.form);
    expect(view.input.value).toBe("");
    fireEvent.submit(view.form);
    expect(accessApi.reauthenticate).toHaveBeenCalledTimes(1);
    expect(accessApi.reauthenticate).toHaveBeenCalledWith(
      context.membership_id,
      " synthetic current password ",
      { signal: expect.any(AbortSignal) },
    );
    expect(view.onConfirmed).not.toHaveBeenCalled();
    await act(async () => finish());
    expect(view.onChanged).toHaveBeenCalledOnce();
    expect(accessApi.session).toHaveBeenCalledOnce();
    expect(view.onConfirmed).toHaveBeenCalledWith(expect.any(Number));
    expect(view.onConfirmed.mock.calls[0][0]).toBeLessThanOrEqual(270_000);
    expect(read).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
  });
  it.each(["user_id", "tenant_id", "membership_id", "role"] as const)(
    "retira contexto si cambia %s después de rotar",
    async (field) => {
      vi.mocked(accessApi.session).mockResolvedValue({
        ...context,
        [field]:
          field === "role" ? "cashier" : "00000000-0000-4000-8000-000000000004",
      });
      const view = open();
      fireEvent.submit(view.form);
      await waitFor(() => expect(view.onProblem).toHaveBeenCalledOnce());
      expect(view.onConfirmed).not.toHaveBeenCalled();
      expect(view.input.value).toBe("");
    },
  );
  it.each([401, 403, 429, 503])(
    "rechaza %s sin eco, replay ni montaje administrativo",
    async (status) => {
      vi.mocked(accessApi.reauthenticate).mockRejectedValue(
        new ApiError("http", "private-canary", status),
      );
      const view = open();
      fireEvent.submit(view.form);
      await waitFor(() => expect(view.onProblem).toHaveBeenCalledOnce());
      expect(view.onProblem.mock.calls[0][0]).not.toContain("private-canary");
      expect(view.onConfirmed).not.toHaveBeenCalled();
      expect(accessApi.session).not.toHaveBeenCalled();
      expect(accessApi.reauthenticate).toHaveBeenCalledOnce();
      expect(view.input.value).toBe("");
    },
  );
  it("desmontar aborta y descarta la confirmación tardía", async () => {
    let finish!: () => void;
    vi.mocked(accessApi.reauthenticate).mockReturnValue(
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
    );
    const view = open();
    fireEvent.submit(view.form);
    const signal = vi.mocked(accessApi.reauthenticate).mock.calls[0][2]!
      .signal!;
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => finish());
    expect(view.onChanged).not.toHaveBeenCalled();
    expect(view.onConfirmed).not.toHaveBeenCalled();
    expect(accessApi.session).not.toHaveBeenCalled();
  });
});
