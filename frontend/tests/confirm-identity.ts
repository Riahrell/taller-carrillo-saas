import { fireEvent, screen } from "@testing-library/react";

export async function confirmIdentity() {
  const input = await screen.findByLabelText("Tu contraseña actual");
  fireEvent.change(input, { target: { value: "synthetic current password" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirmar identidad" }));
}
