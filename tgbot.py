import os
import classes
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp_socks import ProxyConnector
from classes import settings
import db_driver
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
admins = list(map(int, os.getenv("ADMINS", "1234").split(',')))
addr = os.getenv("ADDRESS_PROXY", "127.0.0.110808")
use_proxy = os.getenv("USE_PROXY", "False")

session = AiohttpSession("http://127.0.0.1:10808")
bot = Bot(token=BOT_TOKEN, session=session) if use_proxy=='True' else Bot(token=BOT_TOKEN)
dp = Dispatcher()

def is_admin(id):
    return id in admins

@dp.message(Command("start"))
async def start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Команды:\n"
                         "<code>/info</code> - информация о пользователе\n"
                         "<code>/setlimit</code> - установить максимальное количество устройств по uuid\n"
                         "<code>/clear</code> uuid - очистить список hwid\n"
                         "<code>/allinfo</code> - информация о всех подключениях\n"
                         "<code>/backup</code> - бэкап базы данных\n"
                         "<code>/restset</code> &lt;1, 2, 3...&gt; - задать подключения с ограниченим\n"
                         "<code>/restadd</code> &lt;number&gt; - добавить подключение с огруничением\n"
                         "<code>/restshow</code> - показать подключения с ограничениями\n"
                         "<code>/setgb</code> &lt;number&gt; задать базовое ограничение гб\n",
                         parse_mode="HTML"
                         )

@dp.message(Command("info"))
async def info(message: Message, command: Command):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /info <uuid> or <name>")
        return
    uuid = command.args.strip()
    await message.answer(f"Информация о пользователе:\n{get_info(uuid)}", parse_mode="HTML")

@dp.message(Command("setlimit"))
async def setlimit(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /setlimit <uuid> <max_hwid>")
        return

    parts = command.args.strip().split()
    if len(parts) != 2:
        await message.answer("Использование: /setlimit <uuid> <max_hwid>")
        return
    uuid, limit = command.args.split()
    db_driver.update_max_device_data_by(classes.user(uuid = uuid, max_device=limit))
    await message.answer(f"Изменено:\n{get_info(uuid)}", parse_mode="HTML")

@dp.message(Command("clear"))
async def clear(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /clear <uuid>")
        return
    uuid= command.args.strip()
    db_driver.update_device_data_by(classes.user(uuid = uuid))

    await message.answer(f"Изменено:\n{get_info(uuid)}", parse_mode="HTML")

@dp.message(Command("allinfo"))
async def allinfo(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /allinfo max_hwid")
        return
    mx = command.args.strip()
    try:
        mx = int(mx)
    except Exception:
        await message.answer("Ошибка ввода")
    r = db_driver.find_all_less(mx)
    await message.answer(f"Все записи меньше {mx}:" + "\n" + r, parse_mode="HTML")

@dp.message(Command("backup"))
async def backup(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    from backup_db import backup_db
    await backup_db()

@dp.message(Command("restset"))
async def restset(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /restset <1, 2, 3...>")
        return
    inbounds = command.args.strip().replace(' ', '').split(",")
    settings['restricted_inbounds'] = inbounds
    db_driver.update_settings()
    await message.answer(f"Найстройки изменены. Ограничения действуют для следующих id подключений: {', '.join(inbounds)}",
                         parse_mode="HTML")


@dp.message(Command("restshow"))
async def restshow(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    await message.answer(f"Ограничения действуют для следующих id подключений: "
                         f"{', '.join(settings['restricted_inbounds'])}",
                         parse_mode="HTML")

@dp.message(Command("restadd"))
async def restadd(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /restadd <1, 2, 3...>")
        return
    inbounds = command.args.strip().replace(' ', '').split(",")
    for i in inbounds:
        if i not in settings['restricted_inbounds']:
            settings['restricted_inbounds'].append(i)
    db_driver.update_settings()
    await message.answer(f"Найстройки изменены. Ограничения действуют для следующих id подключений: "
                         f"{', '.join(settings['restricted_inbounds'])}",
                         parse_mode="HTML")

@dp.message(Command("setgbdef"))
async def setgb(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /setgbdef <number>")
        return
    gb = command.args.strip()
    try:
        gb = int(gb)
    except Exception:
        await message.answer(f"Ошибка, использование: /setgbdef <number>")
        return
    settings["base_gb"] = gb * 1024**3
    db_driver.update_settings()
    await message.answer(f"Найстройки изменены. Ограничения действуют для следующих id подключений: "
                         f"{', '.join(settings['restricted_inbounds'])}",
                         parse_mode="HTML")

def get_info(uuid):
    user = db_driver.get_data_by(uuid)
    name = user.name
    mx = user.max_device
    hwds = user.current_device
    uuid = user.uuid

    return (             f"UUID: <code>{uuid}</code>\n"
                         f"Name: <code>{name}</code>\n"
                         f"MAX_HWIDS: {mx}\n"
                         f"CURRENT_HWIDS: {len(hwds)}")
def get_info_by_name(name):
    user = db_driver.get_data_by(name)
    uuid = user.uuid
    name = user.name
    mx = user.max_device
    hwds = user.current_device
    return (f"UUID: <code>{uuid}</code>\n"
            f"Name: <code>{name}</code>\n"
            f"MAX_HWIDS: {mx}\n"
            f"CURRENT_HWIDS: {len(hwds)}")


async def setmsg_admin(text):
    await bot.send_message(admins[0], text)
