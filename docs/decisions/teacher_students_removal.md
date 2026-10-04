---
name: teacher_students removal — done
description: Refactor completed 2026-04-20. Teacher→student visibility now derived from teacher_groups + student.group_id; no separate link table.
type: project
originSessionId: 2307c0b0-35a0-4605-9391-097ec66750f1
---
Completed 2026-04-20 (commits `7492a6a` + `be4ab2d`, deployed to VPS).

**Visibility rule:** ученик виден педагогу ⇔ `student.group_id ∈ teacher_groups[teacher_id]`. Единственная точка правила — `TeacherVisibilityService` (`bot/services/visibility.py`), инжектится как `visibility` в хендлеры.

**Why:** было два источника истины (`teacher_students` + `teacher_groups` + `student.group_id`), которые приходилось синхронизировать вручную через «добавь ученика в свой список». Каждый ученик принадлежит ровно одной группе, педагог прикреплён к группам — дополнительная связка была избыточна.

**How to apply:**
- Когда нужно дать педагогу доступ к ученику — единственный путь: добавить строку в `teacher_groups` (teacher ↔ group). Личных списков нет.
- Замена на чужой группе требует предварительного действия админа, а не ad-hoc «добавления в мой список».
- При удалении педагога нужно чистить `teacher_groups` (`teacher_group_repo.remove_all_for_teacher`) — видимость теперь зависит от этой таблицы.

**Pending ops action:** в Google Sheets прод-таблицы ещё лежит вкладка `teacher_students` (328 строк, больше кодом не читается). По плану через ~неделю стабильной работы её переименовать в `_archived_teacher_students_20260420` и спустя время удалить. Пока не трогали.

**Known limitation:** если админ в заявке «привязать существующего ученика» выбирает ученика, у которого `student.group_id != req.group_id`, педагог не получит видимость автоматически. Хендлер пишет об этом явно и админу, и педагогу.
