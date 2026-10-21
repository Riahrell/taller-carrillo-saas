import type { SessionContext } from "@/lib/access/api";

export function sameContext(a: SessionContext, b: SessionContext): boolean {
  return (
    a.user_id === b.user_id &&
    a.tenant_id === b.tenant_id &&
    a.membership_id === b.membership_id &&
    a.role === b.role
  );
}
