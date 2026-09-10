import type { PayableItem } from "@/lib/domain/types";

export function selectableKeys(items: PayableItem[], childId?: string, from?: string, to?: string): string[] {
  return items
    .filter((item) => item.status === "UNPAID")
    .filter((item) => !childId || item.childId === childId)
    .filter((item) => item.type === "SUBSCRIPTION" ? !from || item.periodMonth >= from.slice(0, 7) : !from || item.date >= from)
    .filter((item) => item.type === "SUBSCRIPTION" ? !to || item.periodMonth <= to.slice(0, 7) : !to || item.date <= to)
    .map((item) => item.payableKey);
}

export function mergeUniqueKeys(current: string[], added: string[]): string[] {
  return [...new Set([...current, ...added])];
}

export function cartTotal(items: PayableItem[], keys: string[]): number {
  const selected = new Set(keys);
  return items.filter((item) => selected.has(item.payableKey)).reduce((sum, item) => sum + item.amount, 0);
}
