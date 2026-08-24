import shutil

from aiogram.types import FSInputFile
from dotenv import load_dotenv
import tgbot
from datetime import datetime, timedelta
import asyncio
from datetime import datetime
import os
load_dotenv()

DB_PATH = os.getenv("DB_PATH", "/opt/BanEveryone/subserver.db")

async def backup_db():
    if not os.path.isfile(DB_PATH):
        await tgbot.setmsg_admin("Ошибка бэкапа - файл базы не найден")
        return
    tmp = None
    try:
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        tmp = f"/tmp/subserver_{stamp}.db"
        shutil.copy2(DB_PATH, tmp)
        caption = f'Бэкап {stamp}'
        await tgbot.bot.send_document(
            chat_id=tgbot.admins[0],
            document=FSInputFile(tmp, filename=f"subserver_{stamp}.db"),
            caption=caption,
        )
    except Exception as e:
        await tgbot.setmsg_admin(f'Ошибка бэкапа - {e}')
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return

def seconds_until_midnight() -> float:
    now = datetime.now()
    nxt = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (nxt - now).total_seconds()

async def backup_schedule():
    await asyncio.sleep(5)
    while True:
        wait = seconds_until_midnight()
        await tgbot.setmsg_admin(f'Bot started. \nNext backup db in {wait} seconds. \nIts {(wait // 60) //  60}h, {(wait // 60) % 60 }m, '
                                 f'{int(wait) % 60}s.')
        await asyncio.sleep(wait)
        try:
            await backup_db()
        except Exception as e:
            print("backup error:", e)
        await asyncio.sleep(2)