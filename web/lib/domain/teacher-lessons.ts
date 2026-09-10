import type { RecordLessonInput, TeacherState } from "@/lib/domain/types";

export const DEMO_TODAY = "2026-07-11";

export function validateRecordLesson(state: TeacherState, input: RecordLessonInput, today = DEMO_TODAY): string | null {
  if (!input.date) return "Выберите дату занятия";
  if (input.date > today) return "Нельзя записать занятие будущей датой";
  if (state.submittedPeriods.includes(input.date.slice(0, 7))) return "Период уже сдан и заблокирован";
  if (input.durationMin <= 0) return "Укажите продолжительность занятия";
  if (input.kind === "GROUP" && !input.groupId) return "Выберите группу";
  if (input.kind === "SOLOIST" && input.studentIds.length !== 1) return "Для соло выберите одного ученика";
  if (input.kind === "PAIR" && input.studentIds.length !== 2) return "Для пары выберите двух учеников";
  if (input.kind === "SHARED" && (input.studentIds.length < 2 || input.studentIds.length > 4)) return "Для совместного занятия выберите от 2 до 4 учеников";

  if (input.kind === "SOLOIST") {
    const studentId = input.studentIds[0];
    const duplicate = state.lessons.some((lesson) => lesson.kind === "SOLOIST" && lesson.date === input.date && lesson.studentIds.includes(studentId));
    if (duplicate) return "У этого ученика уже записано сольное занятие на выбранную дату";
  }
  return null;
}

export function calculateTeacherEarned(kind: RecordLessonInput["kind"], durationMin: number): number {
  const rate = kind === "GROUP" ? 900 : 1_100;
  return Math.round(rate * durationMin / 45);
}

export function lessonTitle(input: RecordLessonInput, state: TeacherState): string {
  if (input.kind === "GROUP") return state.groups.find((group) => group.groupId === input.groupId)?.name ?? "Групповое занятие";
  if (input.kind === "PAIR") return "Парное занятие";
  if (input.kind === "SHARED") return "Совместное занятие";
  return "Сольное занятие";
}
