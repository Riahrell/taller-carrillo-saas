import type { SessionContext } from "@/lib/access/api";
import type { Product } from "@/lib/catalog/api";
import type {
  Balance,
  Count,
  PendingCount,
  HistoryEntry,
} from "@/lib/inventory/api";

export const actor = "00000000-0000-4000-8000-000000000001";
export const target = "00000000-0000-4000-8000-000000000002";
export const context: SessionContext = {
  user_id: actor,
  membership_id: actor,
  tenant_id: target,
  tenant_name: "Empresa A",
  role: "admin",
};
export const product: Product = {
  id: target,
  name: "Filtro de aceite",
  sku: "F-1",
  category_id: null,
  price: "12.34",
  is_active: true,
};
export const empty: Balance = {
  product_id: target,
  quantity: "0.000",
  value: "0.000000",
  average_unit_cost: null,
};
export const command: PendingCount = {
  key: "00000000-0000-4000-8000-000000000003",
  data: {
    product_id: target,
    counted_quantity: "2.000",
    reason: "Conteo inicial",
    declared_unit_cost: "3.000000",
  },
};
export const evidence: Count = {
  id: "00000000-0000-4000-8000-000000000004",
  product_id: target,
  operation_key: command.key,
  before_quantity: "0.000",
  after_quantity: "2.000",
  before_value: "0.000000",
  after_value: "6.000000",
  quantity_delta: "2.000",
  value_delta: "6.000000",
  reason: "Conteo inicial",
  declared_unit_cost: "3.000000",
  created_at: "2026-10-18T20:00:00.000001Z",
};

export const historyEvidence: HistoryEntry = {
  id: evidence.id,
  product_id: evidence.product_id,
  source: "COUNT",
  direction: null,
  source_document_id: evidence.id,
  recorded_by: "admin@example.test",
  reason: evidence.reason,
  before_quantity: evidence.before_quantity,
  after_quantity: evidence.after_quantity,
  before_value: evidence.before_value,
  after_value: evidence.after_value,
  quantity_delta: evidence.quantity_delta,
  value_delta: evidence.value_delta,
  created_at: evidence.created_at,
};
