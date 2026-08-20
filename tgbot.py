import os

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import db_driver
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
admins = list(map(int, os.getenv("ADMINS", "1234").split(',')))
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def is_admin(id):
    return id in admins

@dp.message(Command("start"))
async def start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Команды:\n"
                         "/info - информация о пользователе\n"
                         "/setlimit - установить максимальное количество устройств по uuid\n"
                         "/clear uuid - очистить список hwid\n"
                         )

@dp.message(Command("info"))
async def info(message: Message, command: Command):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /info <uuid>")
        return
    uuid = command.args.strip()
    await message.answer(f"Информация о пользователе:\n{get_info(uuid)}")

@dp.message(Command("setlimit"))
async def setlimit(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /setlimit <uuid> <max_hwid>")
        return

    uuid, limit = command.args.split()
    db_driver.update_max_device_data_by_uuid(uuid, limit)
    await message.answer(f"Изменено:\n{get_info(uuid)}")

@dp.message(Command("clear"))
async def clear(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /clear <uuid>")
        return
    uuid= command.args.strip()
    db_driver.update_device_data_by_uuid(uuid, [])

    await message.answer(f"Изменено:\n{get_info(uuid)}")

def get_info(uuid):
    mx, hwds = db_driver.get_data_by_uuid(uuid)
    return (             f"UUID: {uuid}\n"
                         f"MAX_HWIDS: {mx}\n"
                         f"CURRENT_HWIDS: {len(hwds)}")