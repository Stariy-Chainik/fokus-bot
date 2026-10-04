---
name: Lesson student/teacher names are a denormalized snapshot
description: lessons.student_N_name and teacher_name are snapshots at record time; renames don't rewrite history and that's intentional
type: project
originSessionId: 6cb89ad0-12c2-46dd-b3c8-92fdd764a85f
---
In `lessons`, the columns `student_1_name`, `student_2_name`, `teacher_name` are denormalized snapshots taken when the lesson was recorded. They do NOT update when `students.name` or `teachers.name` is edited later.

**Why:** The user confirmed on 2026-04-18 that this is fine — IDs (`student_1_id`, `teacher_id`) are the source of truth, all business logic joins through them, so a stale `*_name` in historical lesson rows doesn't break anything. Propagating renames backwards through lessons would be work for no gain.

**How to apply:**
- When adding rename flows for `Student` / `Teacher`, do NOT retroactively update `lessons.*_name`. Just update the canonical row.
- If a UI shows a lesson's student/teacher name, that name can legitimately be an older version. This is by design.
- Same reasoning should be applied if new entities get similar denormalized name snapshots elsewhere.
