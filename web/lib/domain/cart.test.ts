import { describe, expect, it } from "vitest";
import { createInitialParentState } from "@/lib/data/mock-data";
import { cartTotal, mergeUniqueKeys, selectableKeys } from "./cart";

describe("payment cart", () => {
  it("selects only unpaid items and keeps subscription unique", () => {
    const state = createInitialParentState();
    const keys = selectableKeys(state.payables, "STU-DEMO-MAX", "2026-07-01", "2026-07-31");
    const merged = mergeUniqueKeys(keys, keys);

    expect(merged).toEqual([
      "SUB:STU-DEMO-MAX:GRP-DEMO-KIDS:2026-07",
      "LESSON:STU-DEMO-MAX:LES-DEMO-013"
    ]);
  });

  it("calculates total from server-shaped payable items", () => {
    const state = createInitialParentState();
    const keys = ["LESSON:STU-DEMO-ALISA:LES-DEMO-012", "SUB:STU-DEMO-MAX:GRP-DEMO-KIDS:2026-07"];

    expect(cartTotal(state.payables, keys)).toBe(4_600);
  });

  it("adds a monthly subscription once when selecting a week", () => {
    const state = createInitialParentState();
    const keys = selectableKeys(state.payables, "STU-DEMO-MAX", "2026-07-11", "2026-07-18");

    expect(keys).toEqual([
      "SUB:STU-DEMO-MAX:GRP-DEMO-KIDS:2026-07",
      "LESSON:STU-DEMO-MAX:LES-DEMO-013"
    ]);
  });
});
