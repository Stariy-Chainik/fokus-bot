import { describe, expect, it } from "vitest";
import { createInitialTeacherState } from "@/lib/data/mock-data";
import { calculateTeacherEarned, validateRecordLesson } from "./teacher-lessons";

describe("teacher lesson rules", () => {
  it("requires a lesson date", () => {
    const state = createInitialTeacherState();
    expect(validateRecordLesson(state, { kind: "GROUP", date: "", durationMin: 60, groupId: "GRP-DEMO-JUNIOR", studentIds: [] })).toContain("дату");
  });

  it("rejects a future date", () => {
    const state = createInitialTeacherState();
    expect(validateRecordLesson(state, { kind: "GROUP", date: "2026-07-12", durationMin: 60, groupId: "GRP-DEMO-JUNIOR", studentIds: [] })).toContain("будущей");
  });

  it("rejects a duplicate solo lesson", () => {
    const state = createInitialTeacherState();
    expect(validateRecordLesson(state, { kind: "SOLOIST", date: "2026-07-10", durationMin: 60, studentIds: ["STU-DEMO-ALISA"] })).toContain("уже записано");
  });

  it("allows pair and group repetitions", () => {
    const state = createInitialTeacherState();
    expect(validateRecordLesson(state, { kind: "PAIR", date: "2026-07-10", durationMin: 60, studentIds: ["STU-DEMO-ALISA", "STU-DEMO-DASHA"] })).toBeNull();
    expect(validateRecordLesson(state, { kind: "GROUP", date: "2026-07-09", durationMin: 60, groupId: "GRP-DEMO-JUNIOR", studentIds: [] })).toBeNull();
  });

  it("uses 45 minute normalization for earnings", () => {
    expect(calculateTeacherEarned("GROUP", 60)).toBe(1_200);
    expect(calculateTeacherEarned("SOLOIST", 60)).toBe(1_467);
  });
});
