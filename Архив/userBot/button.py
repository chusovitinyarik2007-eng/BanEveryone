import hashlib
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _markup(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows if row
        ]
    )


def _nav(back_callback=None):
    row = []
    if back_callback:
        row.append(("⬅️ Назад", back_callback))
    row.append(("🏠 На главную", "main"))
    return row


def get_main_inline_keyboard():
    return _markup([[('🔑 Мои ключи', 'my_keys')]])


def get_home_keyboard():
    return _markup([[('🏠 На главную', 'main')]])


def get_keys_keyboard(users=None, can_renew=False):
    users = users or []
    rows = [[("⚡️ Запросить новый ключ", "add_new_key")]]
    for user in users:
        rows.append([(f"🔑 {user.name}", f"key:{user.uuid}")])
    if can_renew:
        rows.append([("🔄 Продлить все ключи", "renew:all")])
    rows.append(_nav())
    return _markup(rows)


def get_key_keyboard(sub_id: str):
    # Отдельное продление одного ключа намеренно отсутствует.
    return _markup([
        [("📱 Управление устройствами", f"devices:{sub_id}")],
        [("🗑 Удалить ключ", f"keydel:{sub_id}")],
        _nav("my_keys"),
    ])


def get_delete_confirm_keyboard(sub_id: str):
    return _markup([
        [("✅ Да", f"keydelyes:{sub_id}"), ("❌ Нет", f"key:{sub_id}")],
        _nav(f"key:{sub_id}"),
    ])


def device_token(hwid: str) -> str:
    return hashlib.sha256(str(hwid).encode("utf-8")).hexdigest()[:10]


def get_devices_keyboard(user):
    rows = []
    for device in user.current_device or []:
        token = device_token(device.get("hwid") or "")
        model = device.get("device_model") or "Неизвестное устройство"
        rows.append([(f"❌ Отключить {model}", f"devdel:{user.uuid}:{token}")])
    rows.append(_nav(f"key:{user.uuid}"))
    return _markup(rows)


def get_device_confirm_keyboard(sub_id: str, token: str):
    return _markup([
        [("✅ Да", f"devyes:{sub_id}:{token}"), ("❌ Нет", f"devices:{sub_id}")],
        _nav(f"devices:{sub_id}"),
    ])


def get_antiblock_keyboard():
    return _markup([
        [("✅ Да", "newanti:1"), ("❌ Нет", "newanti:0")],
        _nav("my_keys"),
    ])


def get_period_keyboard(prefix="newperiod", cost_per_month=0, discount=0, back_callback="main", callback_extra=None, edit_keys=False):
    monthly = int(cost_per_month or 0)
    discount = max(0, int(discount or 0))

    def title(months, label):
        price = max(0, monthly * months - discount)
        formatted = f"{price:,}".replace(",", " ")
        return f"{label} — {formatted} ₽"

    def data(months):
        if callback_extra is None:
            return f"{prefix}:{months}"
        return f"{prefix}:{callback_extra}:{months}"

    rows = [
        [(title(1, "1 месяц"), data(1)), (title(3, "3 месяца"), data(3))],
        [(title(6, "6 месяцев"), data(6)), (title(12, "1 год"), data(12))],
    ]
    if edit_keys:
        rows.append([("🛠 Редактировать ключи", "my_keys")])
    rows.append(_nav(back_callback))
    return _markup(rows)


def get_request_confirm_keyboard(months: int, anti_block: bool):
    anti = 1 if anti_block else 0
    return _markup([
        [("📨 Запросить", f"newsend:{anti}:{months}")],
        _nav(f"newanti:{anti}"),
    ])


def get_renew_confirm_keyboard(scope: str, months: int):
    return _markup([
        [("📨 Запросить", f"rsend:{scope}:{months}")],
        [("🛠 Редактировать ключи", "my_keys")],
        _nav(f"renew:{scope}"),
    ])


def get_renew_sent_keyboard():
    return _markup([
        [("🛠 Редактировать ключи", "my_keys")],
        [("🏠 На главную", "main")],
    ])


def get_expiry_keyboard(users):
    rows = [[("🔄 Продлить все", "renew:exp")]]
    rows.extend([[(f"🗑 Удалить {u.name}", f"keydel:{u.uuid}")] for u in (users or [])])
    rows.append([("🛠 Редактировать ключи", "my_keys")])
    rows.append([("🏠 На главную", "main")])
    return _markup(rows)
