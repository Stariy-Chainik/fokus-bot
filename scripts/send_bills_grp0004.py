import asyncio
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config.settings import settings
from bot.repositories.sheets_client import SheetsClient
from bot.repositories.student_group_repo import StudentGroupRepository
from bot.repositories.student_repo import StudentRepository
from bot.repositories.group_repo import GroupRepository
from bot.repositories.lesson_repo import LessonRepository
from bot.repositories.teacher_repo import TeacherRepository
from bot.repositories.payment_repo import PaymentRepository
from bot.services.payment_service import PaymentService
from bot.utils.bill_format import build_bill_text

GROUP_ID = "GRP-0004"
PERIOD = "2026-05"
RECIPIENT = 664410718  # Контарева Елизавета (TCH-0009)


async def main():
    sc = SheetsClient(settings)
    sgrepo = StudentGroupRepository(sc, settings.sheet_student_groups)
    srepo = StudentRepository(sc, settings.sheet_students)
    grepo = GroupRepository(sc, settings.sheet_groups)
    lrepo = LessonRepository(sc, settings.sheet_lessons)
    trepo = TeacherRepository(sc, settings.sheet_teachers)
    prepo = PaymentRepository(sc, settings.sheet_payments)
    ps = PaymentService(prepo, lrepo, trepo, group_repo=grepo, student_group_repo=sgrepo)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    sids = await sgrepo.get_students_for_group(GROUP_ID)
    sent = 0
    try:
        intro = (
            "📋 <b>Счета учеников группы «БП Детская спортивная» за май 2026</b>\n"
            "Ниже — отдельный счёт на каждого ученика."
        )
        await bot.send_message(RECIPIENT, intro)

        for sid in sids:
            s = await srepo.get_by_id(sid)
            if not s:
                continue
            bills = await ps.compute_bills_for_student_period(sid, PERIOD)
            if not bills:
                continue
            gids = await sgrepo.get_groups_for_student(sid)
            gnames = []
            for gid in gids:
                g = await grepo.get_by_id(gid)
                if g:
                    gnames.append(g.name)
            text, total = build_bill_text(s.name, gnames, PERIOD, bills)
            await bot.send_message(RECIPIENT, text)
            sent += 1
            print(f"OK {sid} {s.name}: {total} руб")
            await asyncio.sleep(0.4)
        print(f"Отправлено счетов: {sent}")
    finally:
        await bot.session.close()


asyncio.run(main())
