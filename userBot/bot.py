import asyncio
import os
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from dotenv import load_dotenv

import db_driver
import xui_connection
from classes import settings
from subscription_service import (
    PERIODS,
    detach_subscription,
    list_inbounds,
    public_subscription_url,
    remove_device as service_remove_device,
    price_for_months,
    format_price,
    monthly_price,
)
from userBot.button import *

load_dotenv()
BOT_TOKEN = os.getenv("USER_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("USER_BOT_TOKEN не указан в .env")
PROXY_ADDRESS = os.getenv("ADDRESS_PROXY", "http://127.0.0.1:10808")
USE_PROXY = os.getenv("USE_PROXY", "false").strip().lower() in ("true", "1", "yes", "on")
session = AiohttpSession(proxy=PROXY_ADDRESS) if USE_PROXY else None
bot = Bot(token=BOT_TOKEN, session=session) if session else Bot(token=BOT_TOKEN)
dp = Dispatcher()


def _days_text(expiry_ms: int) -> str:
    expiry_ms = int(expiry_ms or 0)
    if expiry_ms <= 0:
        return "Без ограничения по сроку"
    now_ms = int(datetime.now().timestamp() * 1000)
    if expiry_ms <= now_ms:
        return "Срок действия закончился"
    days = max(1, (expiry_ms - now_ms + 86_399_999) // 86_400_000)
    return f"Осталось примерно {days} дн."


async def _panel_users():
    try:
        return await xui_connection.get_users_(False)
    except Exception:
        # Если панель временно недоступна, не удаляем ключи из интерфейса только
        # из-за сетевой ошибки. Возвращаем None и используем локальные данные.
        return None


async def get_visible_users(tg_id: int):
    local_users = db_driver.get_data_by_tg_id(tg_id) or []
    panel = await _panel_users()
    if panel is None:
        return local_users

    result = []
    for user in local_users:
        p = panel.get(user.name)
        if not p:
            continue
        if str(p.get("subId") or "") != str(user.uuid):
            continue
        result.append(user)
    return result


async def _owned(sub_id: str, tg_id: int):
    user = db_driver.get_owned_subscription(sub_id, tg_id)
    if not user:
        return None
    panel = await _panel_users()
    if panel is None:
        return user
    p = panel.get(user.name)
    if not p or str(p.get("subId") or "") != str(user.uuid):
        return None
    return user


async def get_main_text(tg_id: int):
    users = await get_visible_users(tg_id)
    return (
        f"<b>Привет</b> 👋\n"
        f"Здесь можно управлять VPN-подписками, подключёнными устройствами, "
        f"продлевать ключи и запрашивать новые.\n\n"
        f"🔑 Ваши ключи: <b>{len(users)}</b>\n\n"
        f"❗️Если какой-то ключ не отображается, напишите администратору: @yar1k_ch"
    )


async def get_subscription_details(user, panel=None):
    restricted = {str(x) for x in settings.get("restricted_inbounds", [])}
    panel = panel if panel is not None else await _panel_users()

    base = panel.get(user.name) if isinstance(panel, dict) else None
    rest = panel.get(user.name + "--rest") if isinstance(panel, dict) else None

    if panel is not None and not base:
        return {
            "exists": False,
            "anti": False,
            "expiry": 0,
            "days": "Ключ недоступен",
            "url": public_subscription_url(user.uuid),
        }

    expiry = int((base or {}).get("expiryTime") or 0)
    base_ids = (base or {}).get("id_inbounds") or []
    rest_ids = (rest or {}).get("id_inbounds") or []
    anti = bool(rest) or any(str(x) in restricted for x in base_ids + rest_ids)

    return {
        "exists": True,
        "anti": anti,
        "expiry": expiry,
        "days": _days_text(expiry),
        "url": public_subscription_url(user.uuid),
    }


async def render_keys_text(users):
    if not users:
        return "<b>Мои ключи</b> 🔑\n\nУ вас пока нет подписок."

    panel = await _panel_users()
    blocks = ["<b>Мои ключи</b> 🔑"]
    for user in users:
        details = await get_subscription_details(user, panel)
        if not details["exists"]:
            continue
        plan = "⚡️ С Антиглушилкой" if details["anti"] else "Обычная"
        blocks.append(
            f"👤 <b>{escape(str(user.name))}</b>\n"
            f"  |— Тип: <b>{plan}</b>\n"
            f"  |— Срок: <b>{escape(details['days'])}</b>\n"
            f"  |— Устройства: <b>{len(user.current_device or [])}/{user.max_device}</b>\n"
            f"  |— Ссылка: <code>{escape(details['url'])}</code>"
        )
    return "\n\n".join(blocks)


async def get_renewable_users(users):
    """Возвращает только ключи с конечным сроком действия.

    expiryTime <= 0 означает бессрочную подписку. Такие ключи нельзя
    продлевать через бота и они никогда не должны попадать в заявку.
    """
    panel = await _panel_users()
    result = []
    for user in users or []:
        details = await get_subscription_details(user, panel)
        if not details["exists"]:
            continue
        if int(details.get("expiry") or 0) <= 0:
            continue
        result.append(user)
    return result


async def get_renew_monthly_total(users) -> int:
    panel = await _panel_users()
    total = 0
    for user in users:
        details = await get_subscription_details(user, panel)
        if details["exists"] and int(details.get("expiry") or 0) > 0:
            total += monthly_price(details["anti"])
    return total


async def get_expiring_owned_subscriptions(tg_id: int):
    users = await get_visible_users(tg_id)
    result = []
    thresholds = [int(x) for x in settings.get("alert_days", [])]
    max_threshold = max(thresholds, default=0)
    now_ms = int(datetime.now().timestamp() * 1000)
    panel = await _panel_users()
    for user in users:
        details = await get_subscription_details(user, panel)
        expiry = details["expiry"]
        if not expiry or expiry <= now_ms:
            continue
        days_left = max(0, (expiry - now_ms + 86_399_999) // 86_400_000)
        if days_left <= max_threshold:
            result.append(user)
    return result


def _renew_discount(tg_id: int, subtotal: int):
    configured, discount_type = db_driver.get_effective_discount(tg_id)
    applied = min(max(0, int(configured)), max(0, int(subtotal)))
    return applied, max(0, int(subtotal) - applied), discount_type


def _price_lines(subtotal: int, discount: int, total: int):
    lines = []
    if discount:
        lines.append(f"💲 Стоимость без скидки: <b>{format_price(subtotal)}</b>")
        lines.append(f"☺️ Ваша скидка: <b>−{format_price(discount)}</b>")
    lines.append(f"🟢 Итого: <b>{format_price(total)}</b>")
    return "\n".join(lines)


async def _resolve_renew_scope(scope: str, tg_id: int):
    # Продление отдельных ключей запрещено. Только группа ключей.
    # Бессрочные подписки (expiryTime <= 0) всегда исключаются.
    if scope == "all":
        return await get_renewable_users(await get_visible_users(tg_id))
    if scope == "exp":
        return await get_renewable_users(await get_expiring_owned_subscriptions(tg_id))
    return []


def _renew_back(scope: str):
    return "my_keys"


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        await get_main_text(message.from_user.id),
        parse_mode="HTML",
        reply_markup=get_main_inline_keyboard(),
    )


@dp.callback_query(F.data == "main")
async def load_main(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        await get_main_text(callback.from_user.id),
        parse_mode="HTML",
        reply_markup=get_main_inline_keyboard(),
    )


@dp.callback_query(F.data == "my_keys")
async def my_keys(callback: CallbackQuery):
    await callback.answer()
    users = await get_visible_users(callback.from_user.id)
    renewable_users = await get_renewable_users(users)
    text = await render_keys_text(users)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=get_keys_keyboard(users, can_renew=bool(renewable_users)),
    )


@dp.callback_query(F.data.startswith("key:"))
async def key_details(callback: CallbackQuery):
    await callback.answer()
    sub_id = callback.data.split(":", 1)[1]
    user = await _owned(sub_id, callback.from_user.id)
    if not user:
        await callback.answer("Ключ больше недоступен", show_alert=True)
        await my_keys(callback)
        return
    details = await get_subscription_details(user)
    plan = "⚡️ С Антиглушилкой" if details["anti"] else "Обычная"
    text = (
        f"🔑 <b>{escape(str(user.name))}</b>\n\n"
        f"🔵 Тип: <b>{plan}</b>\n"
        f"⏳ Срок: <b>{escape(details['days'])}</b>\n"
        f"📱 Устройств: <b>{len(user.current_device or [])}/{user.max_device}</b>\n\n"
        f"🔗 <b>Ссылка на подписку:</b>\n<code>{escape(details['url'])}</code>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_key_keyboard(sub_id))


@dp.callback_query(F.data.startswith("keydel:"))
async def delete_one_sub(callback: CallbackQuery):
    await callback.answer()
    sub_id = callback.data.split(":", 1)[1]
    user = await _owned(sub_id, callback.from_user.id)
    if not user:
        await callback.answer("Ключ больше недоступен", show_alert=True)
        return
    await callback.message.edit_text(
        f"❌ Удалить ключ <b>{escape(str(user.name))}</b> из вашего аккаунта?\n\n"
        "Сам ключ продолжит работать, но больше не будет отображаться в боте.",
        parse_mode="HTML",
        reply_markup=get_delete_confirm_keyboard(sub_id),
    )


@dp.callback_query(F.data.startswith("keydelyes:"))
async def delete_one_sub_confirm(callback: CallbackQuery):
    await callback.answer()
    sub_id = callback.data.split(":", 1)[1]
    try:
        await detach_subscription(sub_id, callback.from_user.id)
    except Exception:
        await callback.message.edit_text(
            "❌ Не удалось удалить ключ из аккаунта. Попробуйте позже или напишите администратору: @yar1k_ch",
            reply_markup=get_home_keyboard(),
        )
        return
    await callback.message.edit_text(
        "✅ Ключ удалён из вашего аккаунта. Сам VPN-ключ продолжает работать.",
        reply_markup=get_home_keyboard(),
    )


@dp.callback_query(F.data.startswith("devices:"))
async def devices_menu(callback: CallbackQuery):
    await callback.answer()
    sub_id = callback.data.split(":", 1)[1]
    user = await _owned(sub_id, callback.from_user.id)
    if not user:
        await callback.answer("❌ Ключ больше недоступен", show_alert=True)
        return
    devices = user.current_device or []
    lines = [f"📱 <b>Устройства — {escape(str(user.name))}</b>"]
    if not devices:
        lines.append("\n🔴 Подключённых устройств пока нет.")
    else:
        lines.append("")
        for i, device in enumerate(devices, 1):
            lines.append(
                f"{i}. <b>{escape(str(device.get('device_model') or 'Неизвестное устройство'))}</b>\n"
                f"   OS: {escape(str(device.get('device_os') or 'Неизвестно'))}"
            )
    await callback.message.edit_text(
        "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=get_devices_keyboard(user),
    )


def _find_device_by_token(user, token: str):
    for device in (user.current_device or []):
        if device_token(device.get("hwid") or "") == token:
            return device
    return None


@dp.callback_query(F.data.startswith("devdel:"))
async def remove_device_confirm(callback: CallbackQuery):
    await callback.answer()
    try:
        _, sub_id, token = callback.data.split(":", 2)
    except Exception:
        return
    user = await _owned(sub_id, callback.from_user.id)
    device = _find_device_by_token(user, token) if user else None
    if not device:
        await callback.answer("Устройство не найдено", show_alert=True)
        return
    await callback.message.edit_text(
        "❌ Отключить это устройство?\n\n"
        f"📱 <b>{escape(str(device.get('device_model') or 'Неизвестное устройство'))}</b>\n"
        f"OS: {escape(str(device.get('device_os') or 'Неизвестно'))}\n\n"
        "После отключения обновите подписку в VPN-клиенте на остальных устройствах, "
        "чтобы они получили актуальные данные подключения.",
        parse_mode="HTML",
        reply_markup=get_device_confirm_keyboard(sub_id, token),
    )


@dp.callback_query(F.data.startswith("devyes:"))
async def remove_device_confirm_yes(callback: CallbackQuery):
    await callback.answer()
    try:
        _, sub_id, token = callback.data.split(":", 2)
    except Exception:
        return
    user = await _owned(sub_id, callback.from_user.id)
    device = _find_device_by_token(user, token) if user else None
    if not device:
        await callback.answer("Устройство не найдено", show_alert=True)
        return
    try:
        await service_remove_device(sub_id, str(device.get("hwid") or ""), callback.from_user.id)
    except Exception:
        await callback.message.edit_text(
            "❌ Не удалось отключить устройство. Попробуйте позже или напишите администратору: @yar1k_ch",
            reply_markup=get_key_keyboard(sub_id),
        )
        return
    user = await _owned(sub_id, callback.from_user.id)
    await callback.message.edit_text(
        "✅ Устройство отключено.\n\n"
        "❗️ Обновите подписку в VPN-клиенте на оставшихся устройствах, "
        "чтобы они получили актуальные данные подключения.",
        reply_markup=get_home_keyboard(),
    )


@dp.callback_query(F.data == "add_new_key")
async def add_new_key(callback: CallbackQuery):
    await callback.answer()
    inbounds = await list_inbounds()
    restricted = {str(x) for x in settings.get("restricted_inbounds", [])}
    lines = ["<b>Запрос новой подписки</b>", "", "<b>Доступные подключения:</b>"]
    if inbounds:
        for inbound in inbounds:
            mark = " ⚡️" if str(inbound.get("id")) in restricted else ""
            name = escape(str(inbound.get("remark") or f"Подключение {inbound.get('id')}"))
            lines.append(f"• {name}{mark}")
        lines.extend(["", "⚡️ — Антиглушилка"])
    else:
        lines.append("Подключения временно недоступны")
    lines.extend([
        "",
        "Нужна ли <b>Антиглушилка</b>?",
        f"С Антиглушилкой: <b>{format_price(monthly_price(True))}/мес.</b>",
        f"Без Антиглушилки: <b>{format_price(monthly_price(False))}/мес.</b>",
    ])
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=get_antiblock_keyboard())


@dp.callback_query(F.data.startswith("newanti:"))
async def new_anti(callback: CallbackQuery):
    await callback.answer()
    anti = callback.data.split(":", 1)[1] == "1"
    cost = monthly_price(anti)
    await callback.message.edit_text(
        "<b>Запрос новой подписки</b>\n\n"
        f"Антиглушилка: <b>{'Да' if anti else 'Нет'}</b>\n"
        f"Цена за месяц: <b>{format_price(cost)}</b>\n\n"
        "⏳ Выберите период подписки:",
        parse_mode="HTML",
        reply_markup=get_period_keyboard(
            prefix="newperiod",
            cost_per_month=cost,
            discount=0,
            callback_extra=1 if anti else 0,
            back_callback="add_new_key",
        ),
    )


@dp.callback_query(F.data.startswith("newperiod:"))
async def new_period(callback: CallbackQuery):
    await callback.answer()
    try:
        _, anti_s, months_s = callback.data.split(":", 2)
        anti = anti_s == "1"
        months = int(months_s)
    except Exception:
        return
    inbounds = await list_inbounds()
    restricted = {str(x) for x in settings.get("restricted_inbounds", [])}
    selected = [x for x in inbounds if anti or str(x.get("id")) not in restricted]
    names = []
    for inbound in selected:
        mark = " ⚡️" if str(inbound.get("id")) in restricted else ""
        names.append(escape(str(inbound.get("remark") or inbound.get("id"))) + mark)
    total = price_for_months(months, anti_block=anti)
    await callback.message.edit_text(
        "<b>Проверьте запрос</b> 🤔\n\n"
        f"Антиглушилка: <b>{'Да' if anti else 'Нет'}</b>\n"
        f"Период: <b>{PERIODS[months]}</b>\n"
        f"Подключения: {', '.join(names) or 'нет'}\n\n"
        f"Итого: <b>{format_price(total)}</b>\n\n"
        "Отправить запрос администратору?",
        parse_mode="HTML",
        reply_markup=get_request_confirm_keyboard(months, anti),
    )


@dp.callback_query(F.data.startswith("newsend:"))
async def new_send(callback: CallbackQuery):
    await callback.answer()
    try:
        _, anti_s, months_s = callback.data.split(":", 2)
        anti = anti_s == "1"
        months = int(months_s)
    except Exception:
        return
    total = price_for_months(months, anti_block=anti)
    req_id = db_driver.create_request(
        "new",
        callback.from_user.id,
        callback.from_user.username,
        months,
        anti,
        total_cost=total,
        discount_amount=0,
        discount_type="",
    )
    import tgbot
    await tgbot.send_subscription_request(req_id)
    await callback.message.edit_text(
        "✅ Запрос отправлен администратору. Вы получите уведомление после решения.\n"
        "Для оплаты свяжитесь с администратором: @yar1k_ch",
        reply_markup=get_home_keyboard(),
    )


@dp.callback_query(F.data.startswith("renew:"))
async def renew_start(callback: CallbackQuery):
    await callback.answer()
    scope = callback.data.split(":", 1)[1]
    users = await _resolve_renew_scope(scope, callback.from_user.id)
    if not users:
        await callback.answer("Нет ключей для продления", show_alert=True)
        return
    monthly_total = await get_renew_monthly_total(users)
    configured_discount, discount_type = db_driver.get_effective_discount(callback.from_user.id)
    names = ", ".join(escape(str(u.name)) for u in users)
    discount_label = ""
    if configured_discount:
        kind = "одноразовая" if discount_type == "temporary" else "постоянная"
        discount_label = f"Ваша {kind} скидка: <b>{format_price(configured_discount)}</b>\n"
    await callback.message.edit_text(
        "<b>Продление подписок</b> 🔄\n\n"
        f"Ключи: {names}\n"
        f"Количество: <b>{len(users)}</b>\n"
        f"{discount_label}\n"
        "Выберите период продления:",
        parse_mode="HTML",
        reply_markup=get_period_keyboard(
            prefix="rperiod",
            cost_per_month=monthly_total,
            discount=configured_discount,
            callback_extra=scope,
            back_callback=_renew_back(scope),
            edit_keys=True,
        ),
    )


@dp.callback_query(F.data.startswith("rperiod:"))
async def renew_period(callback: CallbackQuery):
    await callback.answer()
    parts = callback.data.split(":")
    try:
        months = int(parts[-1])
    except Exception:
        return
    scope = ":".join(parts[1:-1])
    users = await _resolve_renew_scope(scope, callback.from_user.id)
    if not users:
        await callback.answer("Нет ключей для продления", show_alert=True)
        return
    subtotal = price_for_months(months, monthly_cost=await get_renew_monthly_total(users))
    discount, total, discount_type = _renew_discount(callback.from_user.id, subtotal)
    names = ", ".join(escape(str(u.name)) for u in users)
    await callback.message.edit_text(
        "<b>Проверьте запрос на продление</b> 🤔\n\n"
        f"Ключи: {names}\n"
        f"Период: <b>{PERIODS[months]}</b>\n\n"
        f"{_price_lines(subtotal, discount, total)}\n\n"
        "Отправить запрос администратору?",
        parse_mode="HTML",
        reply_markup=get_renew_confirm_keyboard(scope, months),
    )


@dp.callback_query(F.data.startswith("rsend:"))
async def renew_send(callback: CallbackQuery):
    await callback.answer()
    if db_driver.has_active_request(callback.from_user.id, "renew"):
        await callback.answer("У вас уже есть запрос на продление, ожидающий решения.", show_alert=True)
        return
    parts = callback.data.split(":")
    try:
        months = int(parts[-1])
    except Exception:
        return
    scope = ":".join(parts[1:-1])
    users = await _resolve_renew_scope(scope, callback.from_user.id)
    if not users:
        await callback.answer("Нет ключей для продления", show_alert=True)
        return
    subtotal = price_for_months(months, monthly_cost=await get_renew_monthly_total(users))
    discount, total, discount_type = _renew_discount(callback.from_user.id, subtotal)
    req_id = db_driver.create_request(
        "renew",
        callback.from_user.id,
        callback.from_user.username,
        months,
        False,
        [u.uuid for u in users],
        total_cost=total,
        discount_amount=discount,
        discount_type=discount_type,
    )
    import tgbot
    await tgbot.send_subscription_request(req_id)
    await callback.message.edit_text(
        "✅ Запрос на продление отправлен администратору. Вы получите уведомление после решения.\n"
        "Для оплаты свяжитесь с администратором: @yar1k_ch\n\n"
        f"Стоимость: <b>{format_price(total)}</b>",
        parse_mode="HTML",
        reply_markup=get_renew_sent_keyboard(),
    )


async def expiry_alert_loop():
    await asyncio.sleep(10)
    while True:
        try:
            grouped = {}
            all_users = await xui_connection.get_users_(False)
            now_ms = int(datetime.now().timestamp() * 1000)
            thresholds = sorted({int(x) for x in settings.get("alert_days", [])})
            for email, panel_user in all_users.items():
                if email.endswith("--rest"):
                    continue
                expiry = int(panel_user.get("expiryTime") or 0)
                sub_id = str(panel_user.get("subId") or "")
                tg_id = int(panel_user.get("tgId") or 0)
                # Бессрочные ключи не требуют предупреждений о завершении.
                if not expiry or expiry <= now_ms or not sub_id or not tg_id:
                    continue
                days_left = max(0, (expiry - now_ms + 86_399_999) // 86_400_000)
                due = [
                    threshold for threshold in thresholds
                    if days_left <= threshold
                    and not db_driver.alert_was_sent(tg_id, sub_id, threshold, expiry)
                ]
                if not due:
                    continue
                user = db_driver.get_owned_subscription(sub_id, tg_id)
                if user:
                    grouped.setdefault(tg_id, {})[sub_id] = (user, days_left, expiry, due)

            for tg_id, entries in grouped.items():
                text = ["⚠️ <b>Скоро заканчивается подписка</b>", ""]
                users = []
                for user, days_left, expiry, due in entries.values():
                    users.append(user)
                    text.append(
                        f"🔑 <b>{escape(str(user.name))}</b>\n"
                        f"Осталось примерно <b>{days_left}</b> дн.\n"
                        f"Ссылка: <code>{escape(public_subscription_url(user.uuid))}</code>"
                    )
                await bot.send_message(
                    tg_id,
                    "\n\n".join(text),
                    parse_mode="HTML",
                    reply_markup=get_expiry_keyboard(users),
                )
                for user, days_left, expiry, due in entries.values():
                    for threshold in due:
                        db_driver.mark_alert_sent(tg_id, user.uuid, threshold, expiry)
        except Exception as e:
            print("expiry_alert_loop error:", e)
        await asyncio.sleep(60 * 60)


async def run_bot():
    while True:
        try:
            await dp.start_polling(
                bot,
                handle_signals=False,
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            print("User bot error:", e)
            await asyncio.sleep(5)
