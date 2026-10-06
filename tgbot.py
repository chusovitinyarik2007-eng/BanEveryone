import os
import shutil
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import classes
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from aiogram.client.session.aiohttp import AiohttpSession
import xui_connection
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

class UploadState(StatesGroup):
    waiting_file = State()

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
                         "<code>/restset</code> &lt;1, 2, 3...&gt; - задать подключения с ограничениями\n"
                         "<code>/restadd</code> &lt;number&gt; - добавить подключение с огруничением\n"
                         "<code>/restshow</code> - показать подключения с ограничениями\n"
                         "<code>/setgb</code> &lt;number&gt; задать базовое ограничение гб\n"
                         "<code>/loaddb</code> - загрузить базу данных\n"
                         "<code>/rmdevice</code> uuid &lt;hwid1, hwid2, ...&gt;",
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
    await xui_connection.sync_names_with_panel()
    await message.answer(f"Информация о пользователе:\n{get_info(uuid, show_all_devices=True)}", parse_mode="HTML")

@dp.message(Command("setlimit"))
async def setlimit(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return

    if not command.args:
        await message.answer("Использование: /setlimit <uuid> <max_hwid>")
        return

    parts = command.args.strip().split()
    await xui_connection.sync_names_with_panel()
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
    await xui_connection.sync_names_with_panel()
    db_driver.update_device_data_by(classes.user(uuid = uuid))

    await message.answer(f"Изменено:\n{get_info(uuid)}", parse_mode="HTML")

@dp.message(Command("rmdevice"))
async def remove_device_hwid(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /rmdevice uuid hwid1, hwid2, hwid3")
        return
    parts = command.args.strip().replace(',', '').split()
    if len(parts) <2:
        await message.answer("Использование: /rmdevice uuid hwid1, hwid2, hwid3")
        return
    uuid = parts[0]
    hwid = parts[1:]
    if db_driver.remove_hwids(uuid, hwid):
        await message.answer(f"Изменено:\n{get_info(uuid, show_all_devices=True)}", parse_mode="HTML")
    else:
        await message.answer(f"Произошла ошибка обновления. Возможно, указанный uuid не найден.", parse_mode="HTML")


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
    await xui_connection.sync_names_with_panel()
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

@dp.message(Command("setgb"))
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

@dp.message(Command("loaddb"))
async def load_database(message: Message, command: CommandObject, state: FSMContext):
    print(1)
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer("Отправьте файл базы данных. Файл должен быть в формате .db")
    await state.set_state(UploadState.waiting_file)

@dp.message(UploadState.waiting_file, F.document)
async def receive_and_apply(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.endswith((".db")):
        await message.answer(
            "Неверный формат файла. Отправьте файл базы данных SQLite (*.db)"
        )
        return
    try:
        from backup_db import backup_db
        await message.answer("Бэкап текущей базы: ")
        await backup_db()
        msg_copy = await message.answer("Скачиваю файл...")
        path = settings['DB_PATH']
        temp_path = path + ".tmp"
        file = await message.bot.get_file(doc.file_id)
        await message.bot.download_file(
            file.file_path,
            temp_path
        )
        await msg_copy.edit_text("Применяю изменения...")
        if os.path.exists(path):
            os.remove(path)
        shutil.move(temp_path, path)

        await state.clear()
        await msg_copy.edit_text("Изменения применены успешно.")
    except Exception as e:
        await msg_copy.edit_text(f"Ошибка обновления базы данных. Текст ошибки:\n{str(e)}")
        if os.path.exists(f"{path}.tmp"):
            os.remove(f"{path}.tmp")
        await state.clear()


def get_info(uuid, show_all_devices=False):
    user = db_driver.get_data_by(uuid)
    name = user.name
    mx = user.max_device
    hwds = user.current_device
    uuid = user.uuid
    if show_all_devices:
        device_info = ""
        for i in hwds:
            device_info += f"<b>OS:</b> {i['device_os']}\n<b>Device:</b> {i['device_model']}\n<b>HWID:</b> <code>{i['hwid']}</code>\n\n"
        return (f"UUID: <code>{uuid}</code>\n"
                f"Name: <code>{name}</code>\n"
                f"MAX_HWIDS: {mx}\n"
                f"DEVICES - Total: {len(hwds)}\n\n{device_info}")
    else:
        return (f"UUID: <code>{uuid}</code>\n"
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


async def sendmsg_admin(text):
    await bot.send_message(admins[0], text)
