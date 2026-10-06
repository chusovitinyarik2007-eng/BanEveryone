from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_main_inline_keyboard():
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="🔑 Мои ключи",
            callback_data="my_keys",
        )
    )

    builder.add(
        InlineKeyboardButton(
            text="📱 Отключить устройство",
            callback_data="remove_device",
        )
    )

    builder.adjust(1)

    return builder.as_markup()


def getinline_keyboard_keys(sub=None):
    if sub is None:
        sub = []

    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="⚡️ Запросить новый ключ",
            callback_data="add_new_key",
        )
    )

    for user in sub:
        builder.add(
            InlineKeyboardButton(
                text=f"❌ Удалить {user.name}",
                callback_data=f"del:{user.uuid}",
            )
        )

    builder.add(
        InlineKeyboardButton(
            text="⬅️ Главная",
            callback_data="main",
        )
    )

    builder.adjust(1)

    return builder.as_markup()


def get_delete_confirm_keyboard(uuid: str):
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="✅ Да",
            callback_data=f"delyes:{uuid}",
        )
    )

    builder.add(
        InlineKeyboardButton(
            text="❌ Нет",
            callback_data="my_keys",
        )
    )

    builder.adjust(2)

    return builder.as_markup()


def get_remove_device_keys_keyboard(users):
    builder = InlineKeyboardBuilder()

    for user in users:
        builder.add(
            InlineKeyboardButton(
                text=f"🔑 {user.name}",
                callback_data=f"rdk:{user.uuid}",
            )
        )

    builder.add(
        InlineKeyboardButton(
            text="⬅️ Главная",
            callback_data="main",
        )
    )

    builder.adjust(1)

    return builder.as_markup()


def get_remove_device_devices_keyboard(user):
    builder = InlineKeyboardBuilder()

    devices = user.current_device or []

    for index, device in enumerate(devices):
        device_model = str(
            device.get(
                "device_model",
                "Неизвестное устройство",
            )
        )

        builder.add(
            InlineKeyboardButton(
                text=f"❌ {device_model}",
                callback_data=f"rd:{user.uuid}:{index}",
            )
        )

    builder.add(
        InlineKeyboardButton(
            text="⬅️ К списку ключей",
            callback_data="remove_device",
        )
    )

    builder.adjust(1)

    return builder.as_markup()


def get_remove_device_confirm_keyboard(
    uuid: str,
    device_index: int,
):
    builder = InlineKeyboardBuilder()

    builder.add(
        InlineKeyboardButton(
            text="✅ Да",
            callback_data=f"rdyes:{uuid}:{device_index}",
        )
    )

    builder.add(
        InlineKeyboardButton(
            text="❌ Нет",
            callback_data=f"rdk:{uuid}",
        )
    )

    builder.adjust(2)

    return builder.as_markup()