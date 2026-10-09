import asyncio
import os
import sqlite3
from datetime import datetime, timedelta

from aiogram.types import FSInputFile
from classes import settings


async def backup_db():
    import tgbot

    source = settings["DB_PATH"]
    if not os.path.isfile(source):
        await tgbot.sendmsg_admin("Ошибка бэкапа - файл базы не найден")
        return

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    tmp = f"/tmp/subserver_{stamp}.db"
    try:
        # SQLite backup API создаёт консистентный snapshot даже при активных записях.
        src = sqlite3.connect(source)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        await tgbot.bot.send_document(
            chat_id=tgbot.admins[0],
            document=FSInputFile(tmp, filename=f"subserver_{stamp}.db"),
            caption=f"Бэкап {stamp}",
        )
    except Exception as e:
        await tgbot.sendmsg_admin(f"Ошибка бэкапа - {e}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def seconds_until_midnight() -> float:
    now = datetime.now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - now).total_seconds()


async def backup_schedule():
    import tgbot

    await asyncio.sleep(5)
    while True:
        wait = seconds_until_midnight()
        await tgbot.sendmsg_admin(
            f"Next backup will be in {wait} seconds.\nAt 00:00:00\n"
            f"Its {(int(wait) // 60) // 60}h, {(int(wait) // 60) % 60}m, {int(wait) % 60}s."
        )
        await asyncio.sleep(wait)
        try:
            await backup_db()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("backup error:", e)
        await asyncio.sleep(2)
