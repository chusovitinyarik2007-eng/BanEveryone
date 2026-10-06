import asyncio
import os
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from dotenv import load_dotenv

import db_driver

from userBot.button import (
    get_main_inline_keyboard,
    getinline_keyboard_keys,
    get_delete_confirm_keyboard,
    get_remove_device_keys_keyboard,
    get_remove_device_devices_keyboard,
    get_remove_device_confirm_keyboard,
)


load_dotenv()


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("USER_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "USER_BOT_TOKEN не указан в .env"
    )


ADMINS = [
    int(value.strip())
    for value in os.getenv("ADMINS", "").split(",")
    if value.strip()
]


PROXY_ADDRESS = os.getenv(
    "ADDRESS_PROXY",
    "http://127.0.0.1:10808",
)


USE_PROXY = (
    os.getenv("USE_PROXY", "false")
    .strip()
    .lower()
    in ("true", "1", "yes", "on")
)


# ============================================================
# BOT INITIALIZATION
# ============================================================

if USE_PROXY:
    session = AiohttpSession(
        proxy=PROXY_ADDRESS
    )

    bot = Bot(
        token=BOT_TOKEN,
        session=session,
    )

else:
    bot = Bot(
        token=BOT_TOKEN,
    )


dp = Dispatcher()


# ============================================================
# SUBSCRIPTIONS
# ============================================================

def get_all_sub(tg_id: int):
    users = db_driver.get_data_by_tg_id(
        tg_id
    ) or []

    if not users:
        return "", []

    lines = []

    for user in users:
        uuid = escape(
            str(user.uuid)
        )

        name = escape(
            str(user.name)
        )

        devices = user.current_device or []

        max_device = user.max_device

        lines.append(
            f"🆔 <code>{uuid}</code>\n"
            f"👤 Имя: {name}\n"
            f"📱 <b>Ограничение:</b> "
            f"{max_device}\n"
            f"🟢 <b>Активно:</b> "
            f"{len(devices)}"
        )

    lines.append(
        f"🔵 <b>Всего подписок: "
        f"{len(users)}</b>"
    )

    return "\n\n".join(lines), users


def get_all_hwids(uuid: str):
    user = db_driver.get_data_by(
        uuid
    )

    if not user:
        return None, None

    devices = user.current_device or []

    if not devices:
        return (
            "У этой подписки пока нет "
            "подключённых устройств.\n\n"
            "✅ <b>Всего устройств: 0</b>",
            user,
        )

    lines = []

    for device in devices:
        device_os = escape(
            str(
                device.get(
                    "device_os",
                    "Неизвестно",
                )
            )
        )

        device_model = escape(
            str(
                device.get(
                    "device_model",
                    "Неизвестно",
                )
            )
        )

        hwid = escape(
            str(
                device.get(
                    "hwid",
                    "Неизвестно",
                )
            )
        )

        lines.append(
            f"🟡 <b>OS:</b> "
            f"{device_os}\n"
            f"📱 <b>Устройство:</b> "
            f"{device_model}\n"
            f"🆔 <b>Уникальный код:</b> "
            f"<code>{hwid}</code>"
        )

    lines.append(
        f"✅ <b>Всего устройств: "
        f"{len(devices)}</b>"
    )

    return "\n\n".join(lines), user


# ============================================================
# TEXTS
# ============================================================

def get_main_text(
    tg_id: int,
) -> str:

    sub_text, users = get_all_sub(
        tg_id
    )

    if not users:
        sub_text = (
            "Подписок пока нет 😢"
        )

    return (
        "<b>Привет</b> 👋\n"
        "В этом боте ты можешь управлять "
        "своими VPN подписками, отвязывать "
        "старые устройства, отправлять запрос "
        "на выпуск нового ключа, получать "
        "напоминания о скором окончании, "
        "а в будущем многое-многое другое! ☺️"
        "\n\n"
        "<b>Твои подписки:</b>\n"
        f"{sub_text}"
        "\n\n"
        "<code>"
        "Если Вы видите не все свои подписки, "
        "пожалуйста, напишите администратору:"
        "</code> "
        "@yar1k_ch"
    )


# ============================================================
# START
# ============================================================

@dp.message(
    Command("start")
)
async def start(
    message: Message,
):
    if not message.from_user:
        return

    await message.answer(
        get_main_text(
            message.from_user.id
        ),
        parse_mode="HTML",
        reply_markup=get_main_inline_keyboard(),
    )


# ============================================================
# MAIN MENU
# ============================================================

@dp.callback_query(
    F.data == "main"
)
async def load_main(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    await callback.message.edit_text(
        get_main_text(
            callback.from_user.id
        ),
        parse_mode="HTML",
        reply_markup=get_main_inline_keyboard(),
    )


# ============================================================
# MY KEYS
# ============================================================

@dp.callback_query(
    F.data == "my_keys"
)
async def my_keys(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    sub_text, users = get_all_sub(
        callback.from_user.id
    )

    if not users:
        await callback.message.edit_text(
            "У вас пока нет подписок 😢"
            "\n\n"
            "Вы можете оформить новый ключ "
            "<b>по кнопке ниже.</b>",
            parse_mode="HTML",
            reply_markup=getinline_keyboard_keys(),
        )

        return

    await callback.message.edit_text(
        f"<b>Ваши ключи:</b>"
        f"\n\n{sub_text}",
        parse_mode="HTML",
        reply_markup=getinline_keyboard_keys(
            users
        ),
    )


# ============================================================
# DELETE SUBSCRIPTION
# ============================================================

@dp.callback_query(
    F.data.startswith("del:")
)
async def delete_one_sub(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    try:
        uuid = callback.data.split(
            ":",
            1,
        )[1]

    except Exception:
        return

    user = db_driver.get_data_by(
        uuid
    )

    if not user:
        await callback.message.edit_text(
            "❌ Ключ не найден.",
            reply_markup=get_main_inline_keyboard(),
        )
        return

    name = escape(
        str(user.name)
    )

    await callback.message.edit_text(
        f"Вы уверены, что хотите удалить "
        f"ключ <b>{name}</b>?",
        parse_mode="HTML",
        reply_markup=(
            get_delete_confirm_keyboard(
                uuid
            )
        ),
    )


# ============================================================
# CONFIRM DELETE
# ============================================================

@dp.callback_query(
    F.data.startswith("delyes:")
)
async def delete_one_sub_confirm(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    try:
        uuid = callback.data.split(
            ":",
            1,
        )[1]

    except Exception:
        return

    await delete_subscription(
        uuid,
        callback,
    )


async def delete_subscription(
    uuid: str,
    callback: CallbackQuery,
):
    """
    Здесь должна быть логика удаления ключа.

    Метод вызывается после нажатия
    пользователем кнопки "Да".
    """

    pass


# ============================================================
# REMOVE DEVICE MENU
# ============================================================

@dp.callback_query(
    F.data == "remove_device"
)
async def remove_device(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    users = db_driver.get_data_by_tg_id(
        callback.from_user.id
    ) or []

    if not users:
        await callback.message.edit_text(
            "У вас пока нет ключей 😢",
            parse_mode="HTML",
            reply_markup=get_main_inline_keyboard(),
        )

        return

    lines = [
        "<b>Отключение устройства</b> 📱",
        "",
        "Выберите ключ, для которого хотите "
        "отключить устройство:",
    ]

    for user in users:
        devices = (
            user.current_device or []
        )

        name = escape(
            str(user.name)
        )

        uuid = escape(
            str(user.uuid)
        )

        lines.append(
            f"🔑 <b>{name}</b>\n"
            f"🆔 <code>{uuid}</code>\n"
            f"📱 Подключено устройств: "
            f"<b>{len(devices)}/"
            f"{user.max_device}</b>"
        )

    await callback.message.edit_text(
        "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=(
            get_remove_device_keys_keyboard(
                users
            )
        ),
    )


# ============================================================
# SELECT KEY
# ============================================================

@dp.callback_query(
    F.data.startswith("rdk:")
)
async def remove_device_select_key(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    try:
        uuid = callback.data.split(
            ":",
            1,
        )[1]

    except Exception:
        return

    user = db_driver.get_data_by(
        uuid
    )

    if not user:
        await callback.message.edit_text(
            "❌ Ключ не найден.",
            reply_markup=get_main_inline_keyboard(),
        )
        return

    devices = (
        user.current_device or []
    )

    name = escape(
        str(user.name)
    )

    if not devices:
        text = (
            f"🔑 <b>{name}</b>"
            "\n\n"
            "У этого ключа нет "
            "подключённых устройств."
        )

    else:
        lines = [
            f"🔑 <b>{name}</b>",
            "",
            "<b>Подключённые устройства:</b>",
        ]

        for index, device in enumerate(
            devices,
            start=1,
        ):
            device_os = escape(
                str(
                    device.get(
                        "device_os",
                        "Неизвестно",
                    )
                )
            )

            device_model = escape(
                str(
                    device.get(
                        "device_model",
                        "Неизвестно",
                    )
                )
            )

            hwid = escape(
                str(
                    device.get(
                        "hwid",
                        "Неизвестно",
                    )
                )
            )

            lines.append(
                f"{index}. 📱 "
                f"<b>{device_model}</b>\n"
                f"   🟡 OS: {device_os}\n"
                f"   🆔 <code>{hwid}</code>"
            )

        text = "\n\n".join(
            lines
        )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=(
            get_remove_device_devices_keyboard(
                user
            )
        ),
    )


# ============================================================
# SELECT DEVICE
# ============================================================

@dp.callback_query(
    F.data.startswith("rd:")
)
async def remove_device_confirm(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    try:
        _, uuid, index_str = (
            callback.data.split(
                ":",
                2,
            )
        )

        device_index = int(
            index_str
        )

    except (
        ValueError,
        AttributeError,
    ):
        return

    user = db_driver.get_data_by(
        uuid
    )

    if not user:
        await callback.message.edit_text(
            "❌ Ключ не найден.",
            reply_markup=get_main_inline_keyboard(),
        )
        return

    devices = (
        user.current_device or []
    )

    if (
        device_index < 0
        or device_index >= len(devices)
    ):
        await callback.message.edit_text(
            "❌ Устройство не найдено.",
            reply_markup=(
                get_remove_device_devices_keyboard(
                    user
                )
            ),
        )
        return

    device = devices[
        device_index
    ]

    device_model = escape(
        str(
            device.get(
                "device_model",
                "Неизвестное устройство",
            )
        )
    )

    device_os = escape(
        str(
            device.get(
                "device_os",
                "Неизвестно",
            )
        )
    )

    hwid = escape(
        str(
            device.get(
                "hwid",
                "Неизвестно",
            )
        )
    )

    await callback.message.edit_text(
        "Вы уверены, что хотите отключить "
        "это устройство?"
        "\n\n"
        f"📱 <b>{device_model}</b>\n"
        f"🟡 OS: {device_os}\n"
        f"🆔 <code>{hwid}</code>",
        parse_mode="HTML",
        reply_markup=(
            get_remove_device_confirm_keyboard(
                uuid,
                device_index,
            )
        ),
    )


# ============================================================
# CONFIRM REMOVE DEVICE
# ============================================================

@dp.callback_query(
    F.data.startswith("rdyes:")
)
async def remove_device_confirm_yes(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    try:
        _, uuid, index_str = (
            callback.data.split(
                ":",
                2,
            )
        )

        device_index = int(
            index_str
        )

    except (
        ValueError,
        AttributeError,
    ):
        return

    user = db_driver.get_data_by(
        uuid
    )

    if not user:
        await callback.message.edit_text(
            "❌ Ключ не найден.",
            reply_markup=get_main_inline_keyboard(),
        )
        return

    devices = (
        user.current_device or []
    )

    if (
        device_index < 0
        or device_index >= len(devices)
    ):
        await callback.message.edit_text(
            "❌ Устройство не найдено.",
            reply_markup=(
                get_remove_device_devices_keyboard(
                    user
                )
            ),
        )
        return

    device = devices[
        device_index
    ]

    hwid = str(
        device.get(
            "hwid",
            "",
        )
    )

    await disconnect_device(
        uuid=uuid,
        hwid=hwid,
        callback=callback,
    )


async def disconnect_device(
    uuid: str,
    hwid: str,
    callback: CallbackQuery,
):
    """
    Здесь должна быть логика
    отключения устройства.

    Вызывается после нажатия "Да".
    """

    pass


# ============================================================
# ADD NEW KEY
# ============================================================

@dp.callback_query(
    F.data == "add_new_key"
)
async def add_new_key(
    callback: CallbackQuery,
):
    await callback.answer()

    if not callback.message:
        return

    await callback.message.edit_text(
        "⚡️ Функция запроса нового ключа "
        "пока не реализована.",
        reply_markup=getinline_keyboard_keys(),
    )


# ============================================================
# RUN
# ============================================================

async def run_bot():
    while True:
        try:
            await dp.start_polling(
                bot
            )

        except asyncio.CancelledError:
            break

        except Exception as e:
            print(
                "User bot error:",
                e,
            )

            await asyncio.sleep(
                5
            )