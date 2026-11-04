import asyncio
import secrets
import os
import shutil
import sqlite3
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import classes
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramRetryAfter
import xui_connection
from classes import settings
import db_driver

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не указан в .env")
admins = [int(x.strip()) for x in os.getenv("ADMINS", "1234").split(",") if x.strip()]
if not admins:
    raise RuntimeError("ADMINS не содержит ни одного Telegram ID")
addr = os.getenv("ADDRESS_PROXY", "http://127.0.0.1:10808")
use_proxy = os.getenv("USE_PROXY", "False").strip().lower() in ("true", "1", "yes", "on")

session = AiohttpSession(proxy=addr) if use_proxy else None
bot = Bot(token=BOT_TOKEN, session=session) if session else Bot(token=BOT_TOKEN)
dp = Dispatcher()

class UploadState(StatesGroup):
    waiting_file = State()


class BroadcastState(StatesGroup):
    waiting_message = State()

def is_admin(id):
    return id in admins

@dp.message(Command("start"))
async def start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    # Telegram deep-link from browser login: admin must explicitly approve.
    argument = (message.text or '').partition(' ')[2].strip()
    if argument.startswith('adm_'):
        import admin_auth
        ident = argument[4:]
        item = admin_auth.challenge_info(ident)
        if message.chat.type != 'private' or message.chat.id != message.from_user.id:
            await message.answer('Подтвердите вход только в личном чате с ботом.')
        elif not item:
            await message.answer('Запрос входа истёк или уже обработан. Откройте сайт заново.')
        else:
            kb = InlineKeyboardBuilder()
            kb.button(text='✅ Подтвердить вход', callback_data='adminweb:yes:' + ident)
            kb.button(text='🚫 Отклонить', callback_data='adminweb:no:' + ident)
            kb.adjust(1)
            await message.answer('🔐 <b>Вход в веб-панель AUTO</b>\n'
                                 f'Код для сверки с браузером: <code>{item["code"]}</code>\n'
                                 f'Время: {item["created"]}\n'
                                 f'IP: <code>{_escape_auto(item["ip"])}</code>\n\n'
                                 'Подтверждайте только запрос, который вы начали сами. '
                                 'Сессия действует 6 часов.',
                                 reply_markup=kb.as_markup(), parse_mode='HTML')
        return
    await message.answer("Команды:\n"
                         "<code>/auto</code> - настройка умной маршрутизации AUTO\n"
                         "<code>/info</code> - информация о пользователе\n"
                         "<code>/setlimit</code> - установить максимальное количество устройств по uuid\n"
                         "<code>/clear</code> uuid - очистить список hwid\n"
                         "<code>/allinfo</code> - информация о всех подключениях\n"
                         "<code>/backup</code> - бэкап базы данных\n"
                         "<code>/restset</code> &lt;1, 2, 3...&gt; - задать подключения с ограничениями\n"
                         "<code>/restadd</code> &lt;number&gt; - добавить подключение с огруничением\n"
                         "<code>/restshow</code> - показать подключения с ограничениями\n"
                         "<code>/setgb</code> &lt;number&gt; задать базовое ограничение гб\n"
                         "<code>/trialdays</code> [1–365] - срок пробной подписки в днях\n"
                         "<code>/prices</code> - показать цены подписок\n"
                         "<code>/setprices</code> &lt;base&gt; &lt;full&gt; - изменить цену без/с Антиглушилкой\n"
                         "<code>/discount</code> &lt;tg_id&gt; &lt;permanent|temporary&gt; &lt;рубли&gt; - скидка на продление\n"
                         "<code>/discountshow</code> &lt;tg_id&gt; - показать скидки клиента\n"
                         "<code>/settype</code> &lt;uuid&gt; &lt;base|full&gt; - изменить тип подписки\n"
                         "<code>/alertset</code> &lt;1,3,...&gt; - дни предупреждений об окончании\n"
                         "<code>/loaddb</code> - загрузить базу данных\n"
                         "<code>/sendtoall</code> - отправить сообщение всем пользователям\n"
                         "<code>/rmdevice</code> uuid &lt;hwid1, hwid2, ...&gt;",
                         parse_mode="HTML"
                         )

@dp.callback_query(F.data.startswith('adminweb:'))
async def web_login_callback(callback: CallbackQuery):
    import admin_auth
    if (not is_admin(callback.from_user.id) or not callback.message
            or callback.message.chat.type != 'private'
            or callback.message.chat.id != callback.from_user.id):
        await callback.answer('Нет доступа', show_alert=True)
        return
    parts = (callback.data or '').split(':', 2)
    if len(parts) != 3 or parts[1] not in ('yes', 'no'):
        await callback.answer('Некорректный запрос', show_alert=True)
        return
    ok = admin_auth.decision(parts[2], callback.from_user.id, parts[1] == 'yes')
    if not ok:
        await callback.answer('Запрос уже обработан или истёк', show_alert=True)
        return
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('✅ Вход подтверждён. Вернитесь в браузер.' if parts[1] == 'yes'
                                  else '🚫 Запрос входа отклонён.')
    await callback.answer()


@dp.message(Command("info"))
async def info(message: Message, command: CommandObject):
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
    if len(parts) != 2:
        await message.answer("Использование: /setlimit <uuid> <max_hwid>")
        return
    uuid, limit_raw = parts
    try:
        limit = int(limit_raw)
        if limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("Лимит должен быть целым неотрицательным числом.")
        return

    await xui_connection.sync_names_with_panel()
    user = db_driver.get_data_by(uuid)
    if not user or user.name == "n/a":
        await message.answer("Подписка не найдена.")
        return
    try:
        from subscription_service import set_subscription_device_limit
        await set_subscription_device_limit(user.uuid, limit)
    except Exception as e:
        from html import escape
        await message.answer(f"Ошибка изменения лимита: <code>{escape(str(e))}</code>", parse_mode="HTML")
        return
    await message.answer(f"Изменено:\n{get_info(user.uuid)}", parse_mode="HTML")


@dp.message(Command("clear"))
async def clear(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /clear <uuid>")
        return

    await xui_connection.sync_names_with_panel()
    user = db_driver.get_data_by(command.args.strip())
    if not user or user.name == "n/a":
        await message.answer("Подписка не найдена.")
        return
    try:
        from subscription_service import rotate_subscription_credentials
        await rotate_subscription_credentials(user.uuid)
        user.current_device = []
        db_driver.update_device_data_by(user)
    except Exception as e:
        from html import escape
        await message.answer(f"Ошибка очистки устройств: <code>{escape(str(e))}</code>", parse_mode="HTML")
        return
    await message.answer(f"Изменено:\n{get_info(user.uuid)}", parse_mode="HTML")


@dp.message(Command("rmdevice"))
async def remove_device_hwid(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /rmdevice uuid hwid1, hwid2, hwid3")
        return
    parts = command.args.strip().replace(',', ' ').split()
    if len(parts) < 2:
        await message.answer("Использование: /rmdevice uuid hwid1, hwid2, hwid3")
        return

    user = db_driver.get_data_by(parts[0])
    hwids = parts[1:]
    if not user or user.name == "n/a":
        await message.answer("Подписка не найдена.")
        return
    remove_set = {str(x) for x in hwids}
    if not any(str(d.get("hwid")) in remove_set for d in (user.current_device or [])):
        await message.answer("Ни одно из указанных устройств не найдено.")
        return
    try:
        from subscription_service import rotate_subscription_credentials
        await rotate_subscription_credentials(user.uuid)
        if not db_driver.remove_hwids(user.uuid, hwids):
            raise RuntimeError("Не удалось обновить локальный список устройств")
    except Exception as e:
        from html import escape
        await message.answer(f"Ошибка удаления устройства: <code>{escape(str(e))}</code>", parse_mode="HTML")
        return
    await message.answer(f"Изменено:\n{get_info(user.uuid, show_all_devices=True)}", parse_mode="HTML")


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
        return
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
    await message.answer(f"Настройки изменены. Базовый лимит трафика: {gb} ГБ.")

@dp.message(Command("trialdays"))
async def trialdays(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args or not command.args.strip():
        await message.answer(
            f"🎁 Срок пробной подписки: {int(settings.get('trial_days', 3))} дн.\n"
            "Для изменения: /trialdays 3 (от 1 до 365 дней).\n"
            "Изменение действует только для новых пробных подписок."
        )
        return
    try:
        raw = command.args.strip()
        if not raw.isdecimal():
            raise ValueError
        days = int(raw)
        if not 1 <= days <= 365:
            raise ValueError
    except ValueError:
        await message.answer("Укажи целое число от 1 до 365. Пример: /trialdays 7")
        return
    settings['trial_days'] = days
    db_driver.update_settings()
    await message.answer(f"✅ Пробный период: {days} дн. Новые ключи будут выдаваться на этот срок.")


@dp.message(Command("prices"))
async def prices(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "💰 <b>Текущие цены за месяц</b>\n\n"
        f"Без Антиглушилки: <b>{int(settings.get('cost_per_month_base', 0))} ₽</b>\n"
        f"С Антиглушилкой: <b>{int(settings.get('cost_per_month_full', 0))} ₽</b>",
        parse_mode="HTML",
    )


@dp.message(Command("setprices"))
async def setprices(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer(
            "Использование: /setprices <base> <full>\n"
            "Например: /setprices 150 200"
        )
        return
    parts = command.args.split()
    if len(parts) != 2:
        await message.answer("Использование: /setprices <base> <full>")
        return
    try:
        base, full = (int(parts[0]), int(parts[1]))
        if base < 0 or full < 0:
            raise ValueError
    except ValueError:
        await message.answer("Цены должны быть целыми неотрицательными числами.")
        return

    settings["cost_per_month_base"] = base
    settings["cost_per_month_full"] = full
    db_driver.update_settings()
    await message.answer(
        "✅ Цены изменены.\n\n"
        f"Без Антиглушилки: <b>{base} ₽/мес.</b>\n"
        f"С Антиглушилкой: <b>{full} ₽/мес.</b>",
        parse_mode="HTML",
    )


@dp.message(Command("discount"))
async def discount(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer(
            "Использование: /discount <telegram_id> <permanent|temporary> <рубли>\n"
            "Например: /discount 123456789 permanent 100\n"
            "Или: /discount 123456789 temporary 250"
        )
        return
    parts = command.args.split()
    # Совместимость: старый формат /discount tg_id amount = permanent.
    if len(parts) == 2:
        parts = [parts[0], "permanent", parts[1]]
    if len(parts) != 3:
        await message.answer("Использование: /discount <telegram_id> <permanent|temporary> <рубли>")
        return
    try:
        tg_id = int(parts[0])
        discount_type = parts[1].strip().lower()
        amount = int(parts[2])
        if discount_type not in {"permanent", "temporary"} or amount < 0:
            raise ValueError
    except ValueError:
        await message.answer("Тип должен быть permanent или temporary, сумма — целым неотрицательным числом.")
        return

    db_driver.set_client_discount(tg_id, amount, discount_type)
    label = "постоянная" if discount_type == "permanent" else "одноразовая"
    if amount == 0:
        await message.answer(
            f"✅ {label.capitalize()} скидка для <code>{tg_id}</code> удалена.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"✅ Для клиента <code>{tg_id}</code> установлена {label} скидка "
            f"<b>{amount} ₽</b> на продление.",
            parse_mode="HTML",
        )


@dp.message(Command("discountshow"))
async def discountshow(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /discountshow <telegram_id>")
        return
    try:
        tg_id = int(command.args.strip())
    except ValueError:
        await message.answer("Telegram ID должен быть целым числом.")
        return
    discounts = db_driver.get_client_discounts(tg_id)
    await message.answer(
        f"Клиент <code>{tg_id}</code>\n"
        f"Постоянная скидка: <b>{discounts['permanent']} ₽</b>\n"
        f"Одноразовая скидка: <b>{discounts['temporary']} ₽</b>\n\n"
        "Если обе скидки заданы, одноразовая имеет приоритет и они не суммируются.",
        parse_mode="HTML",
    )


@dp.message(Command("settype"))
async def settype(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /settype <uuid> <base|full>")
        return
    parts = command.args.strip().split()
    if len(parts) != 2 or parts[1].lower() not in {"base", "full"}:
        await message.answer("Использование: /settype <uuid> <base|full>")
        return
    sub_id, kind = parts[0], parts[1].lower()
    await xui_connection.sync_names_with_panel()
    user = db_driver.get_data_by(sub_id)
    if not user or user.name == "n/a":
        await message.answer("Подписка не найдена.")
        return
    try:
        from subscription_service import set_subscription_type
        await set_subscription_type(user.uuid, kind == "full")
    except Exception as e:
        from html import escape
        await message.answer(
            f"❌ Не удалось изменить тип: <code>{escape(str(e))}</code>",
            parse_mode="HTML",
        )
        return
    await message.answer(
        "✅ Тип подписки изменён на " + ("С Антиглушилкой." if kind == "full" else "Обычная."),
    )


@dp.message(Command("alertset"))
async def alertset(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer(f"Текущие дни предупреждений: {settings.get('alert_days', [])}\nИспользование: /alertset 1,3,7")
        return
    try:
        days = sorted({int(x) for x in command.args.replace(" ", "").split(",") if x != ""})
        if any(x < 0 for x in days):
            raise ValueError
    except ValueError:
        await message.answer("Ошибка. Использование: /alertset 1,3,7")
        return
    settings["alert_days"] = days
    db_driver.update_settings()
    await message.answer(f"Дни предупреждений изменены: {days}")


def _broadcast_cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data="broadcast:cancel",
        )
    )
    return builder.as_markup()


def _broadcast_delete_keyboard(broadcast_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(
        InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=f"broadcast:delete:{broadcast_id}",
        )
    )
    return builder.as_markup()


@dp.message(Command("sendtoall"))
async def send_to_all_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(BroadcastState.waiting_message)
    await message.answer(
        "📨 Отправьте сообщение, которое нужно разослать всем пользователям.",
        reply_markup=_broadcast_cancel_keyboard(),
    )


@dp.callback_query(F.data == "broadcast:cancel")
async def send_to_all_cancel(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    await state.clear()
    await callback.answer("Рассылка отменена")
    if callback.message:
        await callback.message.edit_text("❌ Рассылка отменена.")


@dp.message(BroadcastState.waiting_message)
async def send_to_all_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    # Пока поддерживаем именно сообщения с текстом/подписью. Это позволяет
    # безопасно переносить форматирование между двумя разными Telegram-ботами.
    text = message.html_text or message.html_caption
    if not text:
        await message.answer(
            "Сейчас массовая рассылка поддерживает текстовые сообщения. "
            "Отправьте текст или нажмите «Отменить».",
            reply_markup=_broadcast_cancel_keyboard(),
        )
        return

    recipients = sorted(db_driver.get_all_tg_id())
    if not recipients:
        await state.clear()
        await message.answer("Нет пользователей для рассылки.")
        return

    import userBot.bot as userbot

    broadcast_id = db_driver.create_broadcast(message.from_user.id)
    sent = 0
    failed = 0

    for tg_id in recipients:
        try:
            try:
                sent_message = await userbot.bot.send_message(
                    tg_id,
                    text,
                    parse_mode="HTML",
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(float(exc.retry_after) + 0.1)
                sent_message = await userbot.bot.send_message(
                    tg_id,
                    text,
                    parse_mode="HTML",
                )

            db_driver.add_broadcast_message(
                broadcast_id,
                tg_id,
                sent_message.message_id,
            )
            sent += 1

            # Не упираемся в обычный лимит Bot API при большой базе.
            await asyncio.sleep(0.04)
        except Exception as exc:
            failed += 1
            print(f"Broadcast {broadcast_id}: failed for {tg_id}: {exc}")

    await state.clear()

    if sent == 0:
        db_driver.delete_broadcast(broadcast_id)
        await message.answer(
            f"❌ Не удалось отправить сообщение ни одному пользователю. Ошибок: {failed}."
        )
        return

    result_text = "✅ Сообщение отправлено всем доступным пользователям."
    if failed:
        result_text += f"\n\nОтправлено: {sent}\nНе удалось отправить: {failed}"

    await message.answer(
        result_text,
        reply_markup=_broadcast_delete_keyboard(broadcast_id),
    )


@dp.callback_query(F.data.startswith("broadcast:delete:"))
async def delete_broadcast_messages(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    try:
        broadcast_id = int(callback.data.rsplit(":", 1)[1])
    except (ValueError, AttributeError):
        await callback.answer("Некорректная рассылка", show_alert=True)
        return

    deliveries = db_driver.get_broadcast_messages(broadcast_id)
    if not deliveries:
        await callback.answer("Сообщение уже удалено или рассылка не найдена", show_alert=True)
        return

    import userBot.bot as userbot

    deleted = 0
    failed = 0
    for item in deliveries:
        try:
            await userbot.bot.delete_message(
                chat_id=item["tg_id"],
                message_id=item["message_id"],
            )
            deleted += 1
            await asyncio.sleep(0.04)
        except Exception as exc:
            failed += 1
            print(
                f"Broadcast {broadcast_id}: delete failed for "
                f"{item['tg_id']}:{item['message_id']}: {exc}"
            )

    # После нажатия повторное массовое удаление не требуется. Даже если часть
    # сообщений уже была удалена пользователем, Bot API вернёт ошибку только
    # для конкретного чата, а остальные сообщения всё равно удалятся.
    db_driver.delete_broadcast(broadcast_id)

    await callback.answer("Готово")
    if callback.message:
        text = f"🗑 Рассылка удалена у {deleted} пользователей."
        if failed:
            text += f"\nНе удалось удалить: {failed}."
        await callback.message.edit_text(text)


@dp.message(Command("loaddb"))
async def load_database(message: Message, command: CommandObject, state: FSMContext):
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
        await msg_copy.edit_text("Проверяю базу данных...")
        check_db = sqlite3.connect(temp_path)
        try:
            integrity = check_db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise RuntimeError(f"SQLite integrity_check: {integrity}")
            users_table = check_db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Users'"
            ).fetchone()
            if not users_table:
                raise RuntimeError("В базе отсутствует обязательная таблица Users")
        finally:
            check_db.close()

        await msg_copy.edit_text("Применяю изменения...")
        os.replace(temp_path, path)
        db_driver.init_db()

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


async def send_subscription_request(request_id: int):
    """Отправляет администратору запрос пользователя на новую подписку/продление."""
    req = db_driver.get_request(request_id)
    if not req:
        return
    from subscription_service import PERIODS, list_inbounds, format_price
    username = f"@{req['username']}" if req.get('username') else "без username"
    if req['request_type'] == 'new':
        inbounds = await list_inbounds()
        restricted = {str(x) for x in settings.get('restricted_inbounds', [])}
        selected = [x for x in inbounds if req['anti_block'] or str(x.get('id')) not in restricted]
        names = ", ".join(str(x.get('remark') or x.get('id')) for x in selected) or "нет"
        text = (
            "📨 <b>Запрос новой подписки</b>\n\n"
            f"Пользователь: {username}\n"
            f"Telegram ID: <code>{req['tg_id']}</code>\n"
            f"Период: <b>{PERIODS.get(req['months'], str(req['months']) + ' мес.')}</b>\n"
            f"Скидка: <b>{format_price(req.get('discount_amount', 0))}</b>" + (f" ({'одноразовая' if req.get('discount_type') == 'temporary' else 'постоянная'})" if req.get('discount_amount') else '') + "\n"
            f"Итого: <b>{format_price(req.get('total_cost', 0))}</b>\n"
            f"Антиглушилка: <b>{'Да' if req['anti_block'] else 'Нет'}</b>\n"
            f"Подключения: {names}"
        )
    else:
        users = [db_driver.get_data_by(x) for x in req['sub_ids']]
        names = ", ".join(u.name for u in users if u and u.name != 'n/a') or "нет"
        text = (
            "🔄 <b>Запрос продления</b>\n\n"
            f"Пользователь: {username}\n"
            f"Telegram ID: <code>{req['tg_id']}</code>\n"
            f"Период продления: <b>{PERIODS.get(req['months'], str(req['months']) + ' мес.')}</b>\n"
            f"Скидка: <b>{format_price(req.get('discount_amount', 0))}</b>" + (f" ({'одноразовая' if req.get('discount_type') == 'temporary' else 'постоянная'})" if req.get('discount_amount') else '') + "\n"
            f"Итого: <b>{format_price(req.get('total_cost', 0))}</b>\n"
            f"Подписки: {names}"
        )
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="✅ Принять", callback_data=f"reqok:{request_id}"))
    b.add(InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reqno:{request_id}"))
    b.adjust(2)
    await bot.send_message(admins[0], text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("reqno:"))
async def reject_subscription_request(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    request_id = int(callback.data.split(":", 1)[1])
    req = db_driver.get_request(request_id)
    if not req or req['status'] != 'pending':
        await callback.answer("Запрос уже обработан", show_alert=True)
        return
    if not db_driver.set_request_status(request_id, 'rejected'):
        await callback.answer("Запрос уже обработан", show_alert=True)
        return
    import userBot.bot as userbot
    await userbot.bot.send_message(req['tg_id'], "❌ Администратор отклонил ваш запрос.", reply_markup=userbot.get_home_keyboard())
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.edit_text(callback.message.html_text + "\n\n❌ <b>Отклонено</b>", parse_mode="HTML")
    await callback.answer("Отклонено")


@dp.callback_query(F.data.startswith("reqok:"))
async def approve_subscription_request(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    request_id = int(callback.data.split(":", 1)[1])
    req = db_driver.get_request(request_id)
    if not req or req['status'] != 'pending':
        await callback.answer("Запрос уже обработан", show_alert=True)
        return
    # Резервируем заявку до выполнения операции, чтобы двойной callback
    # не создал две подписки и не продлил одну заявку дважды.
    if not db_driver.transition_request_status(request_id, 'pending', 'processing'):
        await callback.answer("Запрос уже обрабатывается", show_alert=True)
        return
    await callback.answer("Обрабатываю...")
    try:
        from subscription_service import PERIODS, create_subscription, public_subscription_url, renew_subscriptions, format_price
        import userBot.bot as userbot
        if req['request_type'] == 'new':
            sub_id, email, inbound_ids, expiry = await create_subscription(
                req['tg_id'], req.get('username'), req['months'], req['anti_block']
            )
            if not db_driver.transition_request_status(request_id, 'processing', 'approved'):
                raise RuntimeError('Не удалось завершить статус заявки')
            await userbot.bot.send_message(
                req['tg_id'],
                "✅ <b>Подписка активирована!</b>\n\n"
                f"Название: <b>{email}</b>\n"
                f"Период: <b>{PERIODS.get(req['months'], str(req['months']) + ' мес.')}</b>\n"
                + (f"Скидка: <b>{format_price(req.get('discount_amount', 0))}</b>\n" if req.get('discount_amount') else "")
                + f"Итого: <b>{format_price(req.get('total_cost', 0))}</b>\n"
                f"Ссылка:\n<code>{public_subscription_url(sub_id)}</code>",
                parse_mode="HTML",
                reply_markup=userbot.get_home_keyboard(),
            )
        else:
            renewed = await renew_subscriptions(req['sub_ids'], req['tg_id'], req['months'])
            if not db_driver.transition_request_status(request_id, 'processing', 'approved'):
                raise RuntimeError('Не удалось завершить статус заявки')
            # Одноразовая скидка списывается только после успешного продления.
            if req.get('discount_type') == 'temporary' and req.get('discount_amount', 0) > 0:
                db_driver.consume_temporary_discount(req['tg_id'])
            await userbot.bot.send_message(
                req['tg_id'],
                "✅ <b>Продление одобрено.</b>\n\n"
                f"Продлено подписок: <b>{len(renewed)}</b>\n"
                f"Период: <b>{PERIODS.get(req['months'], str(req['months']) + ' мес.')}</b>\n"
                + (f"Скидка: <b>{format_price(req.get('discount_amount', 0))}</b>\n" if req.get('discount_amount') else "")
                + f"Итого: <b>{format_price(req.get('total_cost', 0))}</b>",
                parse_mode="HTML",
                reply_markup=userbot.get_home_keyboard(),
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.edit_text(callback.message.html_text + "\n\n✅ <b>Принято</b>", parse_mode="HTML")
    except Exception as e:
        # Не возвращаем в pending: операция могла выполниться частично, а повторное
        # одобрение тогда создаст дубликат или продлит часть ключей второй раз.
        db_driver.transition_request_status(request_id, 'processing', 'failed')
        from html import escape
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.edit_text(
                callback.message.html_text + "\n\n⚠️ <b>Ошибка, требуется ручная проверка</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass
        await callback.message.answer(
            f"❌ Ошибка выполнения запроса: <code>{escape(str(e))}</code>\n"
            "Заявка помечена как failed, чтобы повторное нажатие не выполнило операцию дважды.",
            parse_mode="HTML",
        )

# --------------------- AUTO / JSON routing (admin only) ---------------------
import smart_routing
import routing_balancers as rb
import auto_rule_input
from html import escape as _escape_auto


class AutoRulesState(StatesGroup):
    waiting_rules = State()
    waiting_auto_name = State()


class AutoBalancerState(StatesGroup):
    waiting_new_name = State()
    waiting_interval = State()
    waiting_name = State()
    waiting_priority = State()


# aiogram handles multiple updates concurrently by default. Serialize changes
# to a particular admin's rules draft, and order fragments by message_id.
_auto_rule_locks = {}


def _rule_lock(user_id):
    return _auto_rule_locks.setdefault(user_id, asyncio.Lock())


def _rule_draft_keyboard(token):
    return _auto_keyboard([
        ("✅ Сохранить все части", f"auto:rulefinish:{token}"),
        ("🧹 Начать ввод заново", f"auto:rulereset:{token}"),
        ("❌ Отмена", f"auto:rulecancel:{token}"),
    ], 1)


def _auto_keyboard(items, width=2):
    kb = InlineKeyboardBuilder()
    for title, action in items:
        kb.button(text=title, callback_data=action)
    kb.adjust(width)
    return kb.as_markup()


def _auto_home():
    switches = [(f"{'✅' if smart_routing.enabled() else '❌'} BASE AUTO", "auto:master")]
    switches.extend([("🗂 Балансировщики", "auto2:list"),
                     (f"⏱ Проверка: {rb.interval()}", "auto2:interval"),
                     ("✏️ Название AUTO", "auto:rename"),
                     ("🚀 Vless config", "av:home"),
                     ("🌐 Веб-панель AUTO", "auto:web"),
                     ("🔄 Обновить", "auto:home")])
    return _auto_keyboard(switches, 2)


async def _auto_render(callback, title, markup):
    await callback.message.edit_text(title, reply_markup=markup, parse_mode="HTML")


async def _panel_auto_nodes():
    rows = await xui_connection.get_inbounds_full()
    return smart_routing.panel_nodes(rows)


def _page_controls(entries, pool, page):
    per_page = 12
    pages = max(1, (len(entries) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    buttons = []
    for n in entries[page * per_page:(page+1)*per_page]:
        mark = "✅" if pool in n['pools'] else "▫️"
        buttons.append((f"{mark} {n['name'][:38]} [{n['id'].split(':')[-1]}]",
                        f"auto:toggle:{pool}:{n['id'].split(':')[-1]}:{page}"))
    if page > 0:
        buttons.append(("⬅️", f"auto:pool:{pool}:{page-1}"))
    if page < pages - 1:
        buttons.append(("➡️", f"auto:pool:{pool}:{page+1}"))
    buttons.append(("◀️ К балансировщикам", "auto:pools"))
    return buttons, page, pages


@dp.message(Command("auto"))
async def auto_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔀 <b>Умные подключения /auto</b>\n"
                         "BASE AUTO — прежняя маршрутизация из 3x-ui.\n"
                         "Vless config — отдельный кешированный профиль из ручных VLESS.\n"
                         "Управлять можно здесь или во вкладках веб-панели.",
                         reply_markup=_auto_keyboard([
                             ("🔀 BASE AUTO", "auto:home"),
                             ("🚀 Vless config", "av:home"),
                             ("🌐 Веб-панель", "auto:web"),
                         ],1), parse_mode="HTML")


@dp.callback_query(F.data.startswith("auto:"))
async def auto_callbacks(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = callback.data.split(":")
    action = data[1]
    if action in ("rulefinish", "rulereset", "rulecancel") and len(data) == 3:
        async with _rule_lock(callback.from_user.id):
            draft = await state.get_data()
            if (await state.get_state() != AutoRulesState.waiting_rules.state
                    or draft.get("auto_rule_token") != data[2]):
                await callback.answer("Это меню устарело. Откройте /auto заново.", show_alert=True)
                return
            await callback.answer()
            if action == "rulecancel":
                await state.clear()
                await callback.message.answer("Редактирование правил отменено. Старые правила сохранены.",
                                              reply_markup=_auto_home())
            elif action == "rulereset":
                await state.update_data(auto_rule_parts=[])
                await callback.message.answer("🧹 Черновик очищен. Отправьте правила заново, "
                                              "затем нажмите «Сохранить все части».",
                                              reply_markup=_rule_draft_keyboard(data[2]))
            else:
                await _auto_finish_rules(callback.message, state, draft)
        return
    if action == 'web':
        admin_url = os.getenv('ADMIN_PUBLIC_URL',
              'https://' + str(settings['HOST']).strip('/') + '/' + settings['sub'].strip('/') + '/adm')
        await callback.answer()
        await callback.message.answer('🌐 Откройте веб-панель AUTO:\n'
                                      + _escape_auto(admin_url) + '\n'
                                      'Доступ после подтверждения входа в этом боте.')
        return
    if action == "master":
        smart_routing.set_option("enabled", not smart_routing.enabled())
    elif action == "switch" and len(data) == 3 and data[2] in smart_routing.POOL_NAMES:
        p = data[2]
        smart_routing.set_option("enabled_" + p, not smart_routing.pool_enabled(p))
    elif action == "toggle" and len(data) == 5 and data[2] in smart_routing.POOL_NAMES:
        try:
            entries = await _panel_auto_nodes()
            node_id = smart_routing.panel_key(data[3])
            if not any(n['id'] == node_id for n in entries):
                raise ValueError("Инбаунд отсутствует в панели")
            smart_routing.toggle_node(node_id, data[2])
            await callback.answer("Состав пула обновлён")
        except (ValueError, RuntimeError) as exc:
            await callback.answer(str(exc)[:150], show_alert=True)
            return
        action, data = "pool", ["auto", "pool", data[2], data[4]]
    elif action in ("rename", "renames") and len(data) == 2:
        await state.set_state(AutoRulesState.waiting_auto_name)
        await callback.answer()
        await callback.message.answer(
            "✏️ <b>Название генерируемого подключения AUTO</b>\n"
            f"Текущее: <b>{_escape_auto(smart_routing.auto_name())}</b>\n\n"
            "Отправьте новое имя одним сообщением.\n"
            "<code>-</code> — восстановить стандартное имя.\n"
            "<code>/cancel</code> — отмена.\n\n"
            "Инбаунды в панели 3x-ui не изменяются.", parse_mode="HTML")
        return
    elif action == "edit" and len(data) == 3 and (data[2] == "ru" or rb.get(data[2])):
        token = secrets.token_hex(5)
        async with _rule_lock(callback.from_user.id):
            await state.set_state(AutoRulesState.waiting_rules)
            await state.set_data({"auto_pool": data[2], "auto_rule_parts": [],
                                  "auto_rule_token": token})
        rules = (smart_routing.pool_rules('ru') if data[2] == 'ru' else rb.get(data[2])['rules'])
        label = 'RU-сайты → DIRECT' if data[2] == 'ru' else rb.get(data[2])['name']
        await callback.answer()
        await callback.message.answer(
            f"📝 Изменение правил <b>{_escape_auto(label)}</b>\n"
            "Отправляйте правила Xray, по одному в строке, <b>сколькими угодно сообщениями</b>.\n"
            "Пример: <code>domain:example.ru</code>\n"
            "Все части накапливаются; до подтверждения текущие правила не меняются.\n"
            "После последней части нажмите «✅ Сохранить все части» или /done.\n"
            "Для очистки правил отправьте <code>-</code> и подтвердите сохранение.\n"
            "Команды: /reset — начать ввод заново, /cancel — выйти без изменений.\n\n"
            f"Текущих правил: {len(rules)}\n"
            + _escape_auto("\n".join(rules)[:1200]),
            reply_markup=_rule_draft_keyboard(token), parse_mode="HTML")
        return
    if action in ("home", "master", "switch"):
        await _auto_render(callback, "🔀 <b>AUTO · глобальные настройки</b>\n"
                           "Выключенный пул исключается из маршрутизации и набора выходов.", _auto_home())
    elif action == "pools":
        names = smart_routing.POOL_NAMES
        buttons = [(f"{v}", f"auto:pool:{p}") for p, v in names.items()]
        buttons.append(("◀️ Назад", "auto:home"))
        await _auto_render(callback, "🗂 <b>Балансировщики AUTO</b>\n"
                           "Выберите балансировщик и отметьте подключения.\n"
                           "«Все серверы» автоматически включает выбранные серверы "
                           "Gemini и «Все сайты»; отдельные узлы можно добавить вручную.\n"
                           "Старые RU-серверы автоматически перенесены в «Все серверы». "
                           "RU-домены направляются в DIRECT.", _auto_keyboard(buttons, 1))
    elif action == "pool" and len(data) in (3, 4) and data[2] in smart_routing.POOL_NAMES:
        p = data[2]
        try:
            entries = await _panel_auto_nodes()
        except Exception as exc:
            await callback.answer(f"Ошибка панели: {str(exc)[:90]}", show_alert=True)
            return
        page = int(data[3]) if len(data) == 4 and data[3].isdigit() else 0
        buttons, page, pages = _page_controls(entries, p, page)
        await _auto_render(callback, f"🗂 <b>{_escape_auto(smart_routing.POOL_NAMES[p])}</b>\n"
                           f"Инбаундов: {len(entries)} · Страница {page+1}/{pages}\n"
                           "✅ Назначен вручную · 🔗 включён через другой основной пул · ▫️ не назначен.\n"
                           "Нажмите на подключение для изменения его прямого назначения.",
                           _auto_keyboard(buttons, 1))
    elif action == "rules" and len(data) == 3 and data[2] in ("ru", "gemini"):
        p = data[2]
        rules = smart_routing.pool_rules(p)
        label = "RU-сайты → DIRECT" if p == "ru" else "Gemini"
        await _auto_render(callback, f"📝 <b>Правила {label}</b>\n"
                           f"Всего: {len(rules)}\n<pre>{_escape_auto(chr(10).join(rules)[:2700])}</pre>",
                           _auto_keyboard([("✏️ Изменить", f"auto:edit:{p}"),
                                           ("◀️ Назад", "auto:home")], 1))
    if action != "pool" or callback.data.startswith("auto:pool:"):
        await callback.answer()


async def _auto_finish_rules(reply_message: Message, state: FSMContext, data: dict):
    """Commit only a complete, validated draft; keep old rules on any error."""
    pool = data.get("auto_pool")
    if pool != 'ru' and not rb.get(pool):
        await reply_message.answer("Балансировщик не найден. Откройте /auto заново.")
        return
    try:
        rules = auto_rule_input.parse_rules(data.get("auto_rule_parts", []))
    except auto_rule_input.RuleInputError as exc:
        await reply_message.answer(str(exc))
        return
    try:
        if pool == 'ru':
            smart_routing.set_option('rules_ru', rules)
        else:
            rb.update(pool, rules=rules)
    except ValueError as exc:
        await reply_message.answer(str(exc))
        return
    await state.clear()
    await reply_message.answer(f"✅ Все правила {pool} сохранены: {len(rules)}. "
                               "Следующее обновление AUTO применит новый список.",
                               reply_markup=_auto_home())


@dp.message(Command("cancel"), AutoRulesState.waiting_rules)
async def auto_cancel_rules(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    async with _rule_lock(message.from_user.id):
        await state.clear()
    await message.answer("Редактирование правил отменено. Старые правила не изменены.",
                         reply_markup=_auto_home())


@dp.message(Command("reset"), AutoRulesState.waiting_rules)
async def auto_reset_rules(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    async with _rule_lock(message.from_user.id):
        data = await state.get_data()
        await state.update_data(auto_rule_parts=[])
    await message.answer("🧹 Черновик очищен. Отправляйте правила заново.",
                         reply_markup=_rule_draft_keyboard(data["auto_rule_token"]))


@dp.message(Command("done"), AutoRulesState.waiting_rules)
async def auto_finish_rules_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    async with _rule_lock(message.from_user.id):
        await _auto_finish_rules(message, state, await state.get_data())


@dp.message(AutoRulesState.waiting_rules)
async def auto_collect_rules(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    if not message.text or message.text.startswith("/"):
        await message.answer("Отправляйте текстовые части правил. /done — сохранить все, "
                             "/reset — начать заново, /cancel — отменить.")
        return
    async with _rule_lock(message.from_user.id):
        data = await state.get_data()
        if data.get('auto_pool') != 'ru' and not rb.get(data.get('auto_pool')):
            await message.answer("Балансировщик не найден. Откройте /auto заново.")
            return
        parts = data.get("auto_rule_parts", [])
        try:
            updated = auto_rule_input.add_part(parts, message.message_id, message.text)
        except auto_rule_input.RuleInputError as exc:
            await message.answer(str(exc))
            return
        if updated == parts:
            return
        await state.update_data(auto_rule_parts=updated)
        count, lines = auto_rule_input.draft_statistics(updated)
        await message.answer(
            f"📥 Получено частей: {count}, строк правил в черновике: {lines}.\n"
            "Отправьте остальные части. После последней нажмите «✅ Сохранить все части» "
            "или /done. Пока ничего не изменено.",
            reply_markup=_rule_draft_keyboard(data["auto_rule_token"]))


@dp.message(Command("cancel"), AutoRulesState.waiting_auto_name)
async def auto_cancel_rename(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Изменение названия AUTO отменено.", reply_markup=_auto_home())


@dp.message(AutoRulesState.waiting_auto_name)
async def auto_save_auto_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip()
    try:
        saved = smart_routing.set_auto_name(None if name == "-" else name)
    except ValueError as exc:
        await message.answer(_escape_auto(str(exc)), parse_mode="HTML")
        return
    await state.clear()
    await message.answer(
        f"✅ Новое название подключения AUTO: <b>{_escape_auto(saved)}</b>\n"
        "Остальные подключения панели 3x-ui не изменены. "
        "Новое имя появится при следующем обновлении JSON-подписки в клиенте.",
        reply_markup=_auto_home(), parse_mode="HTML")

# ---------------- Dynamic balancers: in-bot CRUD and routing -----------------

def _balancer_list_keyboard():
    buttons = []
    for b in rb.all_balancers():
        icon = '✅' if b['enabled'] else '⏸'
        extra = ' 🛟' if b['scope'] == 'reserve' else ''
        buttons.append((f"{icon} {b['name'][:44]}{extra}", f"auto2:open:{b['id']}"))
    buttons.extend([('➕ Создать обычный', 'auto2:new:main'),
                    ('➕ Создать резервный', 'auto2:new:reserve'),
                    ('⏱ Частота проверок', 'auto2:interval'),
                    ('◀️ Назад', 'auto:home')])
    return _auto_keyboard(buttons, 1)


def _balancer_detail_keyboard(b):
    bid = b['id']
    return _auto_keyboard([
        ('🖧 Серверы', f'auto2:nodes:{bid}:0'),
        ('📝 Правила', f'auto:edit:{bid}'),
        ('🔀 Стратегия', f'auto2:strategies:{bid}'),
        ('↪️ Fallback', f'auto2:falloptions:{bid}'),
        (f"{'✅' if b['enabled'] else '⏸'} Включён", f'auto2:enabled:{bid}'),
        (f"⬆️ Выше (−5) · {b['priority']}", f'auto2:up:{bid}'),
        ('⬇️ Ниже (+5)', f'auto2:down:{bid}'),
        ('🔢 Задать число приоритета', f'auto2:priority:{bid}'),
        ('✏️ Переименовать', f'auto2:rename:{bid}'),
        ('🔃 Основной / резервный', f'auto2:scope:{bid}'),
        *([('🗑 Удалить', f'auto2:confirmdelete:{bid}')]
          if bid.startswith('custom_') else []),
        ('◀️ Все балансировщики', 'auto2:list')
    ], 2)


async def _balancer_show(callback, ident):
    b = rb.get(ident)
    if not b:
        await callback.answer('Балансировщик удалён', show_alert=True)
        return
    fb = rb.get(b['fallback'])
    fallback = fb['name'] if fb else {'direct': 'DIRECT', 'block': 'BLOCK', 'none': 'BLOCK'}.get(b['fallback'], b['fallback'])
    label = ('🛟 Только --rest' if b['scope'] == 'reserve' else '🌐 Обычные подключения')
    await _auto_render(callback,
        f"🔀 <b>{_escape_auto(b['name'])}</b>\n"
        f"ID: <code>{b['id']}</code>\n"
        f"Серверы: {label}\n"
        f"Стратегия: <code>{b['strategy']}</code>\n"
        f"Fallback: {_escape_auto(fallback)}\n"
        f"Правил: {len(b['rules'])} · Приоритет: {b['priority']}\n"
        f"Назначенные подключения используются только этим пулом.\n\n"
        "Правила панели DIRECT/BLOCK всегда выше этих правил.", _balancer_detail_keyboard(b))


def _dynamic_node_controls(entries, pool, page):
    per_page = 10
    pages = max(1, (len(entries) + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    buttons = []
    for n in entries[page * per_page:(page + 1) * per_page]:
        status = '✅' if pool in n['pools'] else '▫️'
        buttons.append((f"{status} {n['name'][:36]} [{n['id'].split(':')[-1]}]",
                        f"auto2:toggle:{pool}:{n['id'].split(':')[-1]}:{page}"))
    if page > 0:
        buttons.append(('⬅️', f'auto2:nodes:{pool}:{page-1}'))
    if page < pages - 1:
        buttons.append(('➡️', f'auto2:nodes:{pool}:{page+1}'))
    buttons.append(('◀️ К балансировщику', f'auto2:open:{pool}'))
    return _auto_keyboard(buttons, 1), page, pages


@dp.callback_query(F.data.startswith('auto2:'))
async def auto_dynamic_callbacks(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer('Нет доступа', show_alert=True)
        return
    items = callback.data.split(':')
    action = items[1]
    ident = items[2] if len(items) > 2 else ''
    if action == 'list':
        await _auto_render(callback,
            '🗂 <b>Балансировщики AUTO</b>\n'
            'Можно создавать новые, выбирать правила, инбаунды, стратегию и fallback.\n'
            f'Опрос серверов — общий для AUTO: {rb.interval()}.',
            _balancer_list_keyboard())
    elif action == 'open':
        await _balancer_show(callback, ident)
    elif action in ('new', 'rename'):
        if action == 'new' and ident not in rb.SCOPES:
            await callback.answer('Неверная группа серверов', show_alert=True)
            return
        if action == 'rename' and not rb.get(ident):
            await callback.answer('Пул не найден', show_alert=True)
            return
        await state.set_state(AutoBalancerState.waiting_new_name if action == 'new' else AutoBalancerState.waiting_name)
        await state.set_data({'auto_scope': ident} if action == 'new' else {'auto_id': ident})
        await callback.message.answer('Отправьте название балансировщика (1–70 символов). /cancel — отмена.')
    elif action == 'priority' and rb.get(ident):
        await state.set_state(AutoBalancerState.waiting_priority)
        await state.set_data({'auto_id': ident})
        await callback.message.answer('🔢 Укажи приоритет от -100000 до 100000.\n'
                                     'Чем меньше число, тем раньше проверяются правила.\n'
                                     '/cancel — отмена.')
    elif action == 'interval':
        await state.set_state(AutoBalancerState.waiting_interval)
        await callback.message.answer('⏱ Частота проверки серверов общая для всех пулов.\n'
                                      f'Сейчас: {rb.interval()}.\n'
                                      'Укажите от 10s до 1h, например 30s, 1m или 5m.\n'
                                      '/cancel — отмена.')
    elif action == 'nodes' and len(items) == 4 and rb.get(ident):
        try:
            entries = await _panel_auto_nodes()
        except Exception as exc:
            await callback.answer(f'Не удалось получить инбаунды: {str(exc)[:90]}', show_alert=True)
            return
        keyboard, page, pages = _dynamic_node_controls(entries, ident, int(items[3]) if items[3].isdigit() else 0)
        await _auto_render(callback, f'🖧 <b>{_escape_auto(rb.get(ident)["name"])}</b>\n'
                           f'Инбаундов: {len(entries)}, страница {page+1}/{pages}\n'
                           '✅ Назначен · ▫️ Не назначен · 🔗 унаследован старым пулом «Все серверы».', keyboard)
    elif action == 'toggle' and len(items) == 5 and rb.get(ident):
        try:
            entries = await _panel_auto_nodes()
            panel_id = smart_routing.panel_key(items[3])
            if not any(node['id'] == panel_id for node in entries):
                raise ValueError('Инбаунд отсутствует в панели')
            smart_routing.toggle_node(panel_id, ident)
            await callback.answer('✅ Состав обновлён')
        except (ValueError, RuntimeError) as exc:
            await callback.answer(str(exc)[:160], show_alert=True)
            return
        keyboard, page, pages = _dynamic_node_controls(entries, ident, int(items[4]) if items[4].isdigit() else 0)
        await _auto_render(callback, f'🖧 <b>{_escape_auto(rb.get(ident)["name"])}</b> · {page+1}/{pages}', keyboard)
        return
    elif action == 'strategies' and rb.get(ident):
        b = rb.get(ident)
        await _auto_render(callback, '🔀 <b>Стратегия выбора сервера</b>\n'
                           'leastPing / leastLoad требуют observatory.\n'
                           'random и roundRobin также учитывают доступность при активном observatory.',
                           _auto_keyboard([(f"{'✅' if b['strategy']==st else '▫️'} {st}", f'auto2:strategy:{ident}:{st}')
                                           for st in rb.STRATEGIES] + [('◀️ Назад', f'auto2:open:{ident}')], 1))
    elif action == 'falloptions' and rb.get(ident):
        b = rb.get(ident)
        choices = [('🚫 BLOCK', f'auto2:fall:{ident}:block'),
                   ('🌐 DIRECT', f'auto2:fall:{ident}:direct')]
        for other in rb.all_balancers():
            if other['id'] != ident:
                choices.append((f"{'✅ ' if other['id']==b['fallback'] else ''}↪️ {other['name'][:38]}",
                                f"auto2:fall:{ident}:{other['id']}"))
        choices.append(('◀️ Назад', f'auto2:open:{ident}'))
        await _auto_render(callback, f'↪️ <b>Fallback для {_escape_auto(b["name"])}</b>\n'
                           'Проверка циклов выполняется до сохранения.', _auto_keyboard(choices, 1))
    elif action == 'confirmdelete' and ident.startswith('custom_') and rb.get(ident):
        await _auto_render(callback, '⚠️ Удалить балансировщик и его назначения?\n'
                           'Сам инбаунд панели останется без изменений.',
                           _auto_keyboard([('🗑 Подтверждаю удаление', f'auto2:delete:{ident}'),
                                           ('❌ Отмена', f'auto2:open:{ident}')], 1))
    elif action in ('enabled', 'default', 'up', 'down', 'scope', 'strategy', 'fall', 'delete') and rb.get(ident):
        try:
            b = rb.get(ident)
            if action == 'enabled':
                rb.update(ident, enabled=not b['enabled'])
            elif action == 'default':
                if b['scope'] != 'main':
                    raise ValueError('Основным может быть только обычный балансировщик')
                rb.update(ident, is_default=True)
            elif action in ('up','down'):
                rb.update(ident, priority=b['priority'] + (-5 if action == 'up' else 5))
            elif action == 'scope':
                if b['is_default']:
                    raise ValueError('Сначала назначьте другой балансировщик основным')
                rb.update(ident, scope='main' if b['scope'] == 'reserve' else 'reserve')
            elif action == 'strategy' and len(items) == 4:
                rb.update(ident, strategy=items[3])
            elif action == 'fall' and len(items) == 4:
                rb.update(ident, fallback=items[3])
            elif action == 'delete':
                rb.delete(ident)
                await _auto_render(callback, '✅ Балансировщик удалён', _balancer_list_keyboard())
                await callback.answer()
                return
            else:
                raise ValueError('Неизвестная настройка')
        except ValueError as exc:
            await callback.answer(str(exc)[:190], show_alert=True)
            return
        await _balancer_show(callback, ident)
    else:
        await callback.answer('Действие устарело. Откройте /auto заново.', show_alert=True)
        return
    await callback.answer()


@dp.message(Command('cancel'), AutoBalancerState.waiting_new_name)
@dp.message(Command('cancel'), AutoBalancerState.waiting_name)
@dp.message(Command('cancel'), AutoBalancerState.waiting_interval)
@dp.message(Command('cancel'), AutoBalancerState.waiting_priority)
async def auto_dynamic_cancel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await message.answer('Отменено, ничего не изменено.', reply_markup=_auto_home())


@dp.message(AutoBalancerState.waiting_new_name)
@dp.message(AutoBalancerState.waiting_name)
@dp.message(AutoBalancerState.waiting_interval)
@dp.message(AutoBalancerState.waiting_priority)
async def auto_dynamic_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    mode = await state.get_state()
    data = await state.get_data()
    value = (message.text or '').strip()
    try:
        if mode == AutoBalancerState.waiting_new_name.state:
            ident = rb.create(value, data.get('auto_scope', 'main'))
            text = f'✅ Создан балансировщик «{rb.get(ident)["name"]}» (ID {ident}).\nНазначьте серверы и правила.'
        elif mode == AutoBalancerState.waiting_name.state:
            ident = data.get('auto_id')
            rb.update(ident, name=value)
            text = f'✅ Название изменено: {rb.get(ident)["name"]}'
        elif mode == AutoBalancerState.waiting_interval.state:
            rb.set_interval(value)
            text = f'✅ Новый интервал опроса: {rb.interval()}'
        elif mode == AutoBalancerState.waiting_priority.state:
            if not value.lstrip('-').isdigit():
                raise ValueError('Приоритет должен быть целым числом')
            rb.update(data.get('auto_id'), priority=int(value))
            text = f'✅ Приоритет: {rb.get(data.get("auto_id"))["priority"]}'
        else:
            return
    except ValueError as exc:
        await message.answer(_escape_auto(str(exc)))
        return
    await state.clear()
    await message.answer(_escape_auto(text), reply_markup=_auto_home())

# ---------------------- Vless config: second /auto profile ------------------
import vless_config as vc


class VlessBotState(StatesGroup):
    waiting_links = State()
    waiting_profile_name = State()
    waiting_node_name = State()
    waiting_node_uri = State()
    waiting_probe = State()
    waiting_rules = State()


def _vless_keyboard(page=0):
    st = vc.state()
    items = [
        (('✅ ' if st['enabled'] else '❌ ') + 'Vless config', 'av:toggle'),
        (f"📦 Ключи: {len(st['nodes'])} · включено {st['active_nodes']}", 'av:list:0'),
        ('➕ Добавить VLESS', 'av:add'),
        ('✏️ Название', 'av:profile'),
        (f"⚖️ {st['strategy']}", 'av:strategies'),
        (f"⏱ {st['interval']}", 'av:intervals'),
        ('🌐 URL проверки', 'av:probe'),
        (f"🌍 DIRECT: {len(st['direct_rules'])} правил", 'av:rules'),
        ('🗑 Удалить все ключи', 'av:clear'),
        ('🔀 BASE AUTO', 'auto:home'),
    ]
    return _auto_keyboard(items, 2)


def _vless_node_keyboard(st, page=0):
    nodes = st['nodes']; pages=max(1,(len(nodes)+6)//7); page=max(0,min(page,pages-1))
    items = [(('✅ ' if n['enabled'] else '▫️ ') + n['name'][:38], f"av:node:{n['id']}:{page}")
             for n in nodes[page*7:(page+1)*7]]
    if page: items.append(('⬅️',f'av:list:{page-1}'))
    if page+1<pages: items.append(('➡️',f'av:list:{page+1}'))
    items.append(('◀️ Vless config', 'av:home'))
    return _auto_keyboard(items,1),page,pages


@dp.callback_query(F.data.startswith('av:'))
async def vless_bot_callbacks(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id) or not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Управление разрешено только администратору в личном чате',show_alert=True)
        return
    fields = (callback.data or '').split(':')
    action = fields[1] if len(fields)>1 else ''
    st = vc.state()
    if action == 'home':
        await _auto_render(callback,
            f"🚀 <b>Vless config</b> · отдельный JSON-профиль\n"
            f"Включён: {'да' if st['enabled'] else 'нет'} · ключей: {len(st['nodes'])}\n"
            "Конфигурация сохраняется при изменениях; клиентам отдается без сборки.\n"
            "Только при действующей Антиглушилке --rest.", _vless_keyboard())
    elif action == 'toggle':
        vc.update_settings(enabled=not st['enabled'])
        await _auto_render(callback,'🚀 <b>Vless config</b> · состояние изменено',_vless_keyboard())
    elif action == 'list':
        index=int(fields[2]) if len(fields)>2 and fields[2].isdigit() else 0
        kb,page,pages=_vless_node_keyboard(st,index)
        await _auto_render(callback,f'📦 <b>Вручную добавленные VLESS</b> · {page+1}/{pages}\n'
                           'Выбери сервер для изменения.',kb)
    elif action == 'node' and len(fields)>=3:
        node=next((n for n in st['nodes'] if n['id']==fields[2]),None)
        if not node:
            await callback.answer('Сервер уже удалён',show_alert=True);return
        page=fields[3] if len(fields)>3 and fields[3].isdigit() else '0'
        actions=[(('🟢 Выключить' if node['enabled'] else '🟢 Включить'),f'av:on:{node["id"]}:{page}'),
                 ('✏️ Имя', f'av:editname:{node["id"]}:{page}'),
                 ('🔗 Заменить URI', f'av:edituri:{node["id"]}:{page}'),
                 ('🗑 Удалить', f'av:delask:{node["id"]}:{page}'),
                 ('◀️ Список',f'av:list:{page}')]
        await _auto_render(callback,f"🔐 <b>{_escape_auto(node['name'])}</b>\n"
                           f"Состояние: {'включён' if node['enabled'] else 'выключен'}\n"
                           'Сам ключ не показывается в сообщении; редактирование в личном чате.',
                           _auto_keyboard(actions,1))
    elif action=='on' and len(fields)>=3:
        node=next((n for n in st['nodes'] if n['id']==fields[2]),None)
        if node: vc.update_node(node['id'],enabled=not node['enabled'])
        kb,_,_=_vless_node_keyboard(vc.state(),int(fields[3]) if len(fields)>3 and fields[3].isdigit() else 0)
        await _auto_render(callback,'✅ Изменение сохранено · JSON пересобран',kb)
    elif action=='delask' and len(fields)>=3:
        await _auto_render(callback,'⚠️ Удалить этот сервер?',_auto_keyboard([
            ('✅ Да, удалить',f'av:delete:{fields[2]}:{fields[3] if len(fields)>3 else "0"}'),
            ('❌ Нет',f'av:node:{fields[2]}:{fields[3] if len(fields)>3 else "0"}')],1))
    elif action=='delete' and len(fields)>=3:
        try: vc.delete_node(fields[2])
        except ValueError as exc:
            await callback.answer(str(exc),show_alert=True);return
        kb,_,_=_vless_node_keyboard(vc.state(),int(fields[3]) if len(fields)>3 and fields[3].isdigit() else 0)
        await _auto_render(callback,'✅ Сервер удалён; JSON обновлён',kb)
    elif action=='clear':
        await _auto_render(callback,'⚠️ <b>Удалить все добавленные вручную VLESS?</b>\n'
                           'Действие нельзя отменить.',_auto_keyboard([
                               ('🗑 Подтверждаю удаление', 'av:clearconfirm'),
                               ('❌ Отмена', 'av:home')],1))
    elif action=='clearconfirm':
        vc.clear_nodes()
        await _auto_render(callback,'✅ Все VLESS-ключи удалены',_vless_keyboard())
    elif action in ('add','profile','probe','rules','editname','edituri'):
        modes={'add':VlessBotState.waiting_links,
               'profile':VlessBotState.waiting_profile_name,
               'probe':VlessBotState.waiting_probe,
               'rules':VlessBotState.waiting_rules,
               'editname':VlessBotState.waiting_node_name,
               'edituri':VlessBotState.waiting_node_uri}
        await state.set_state(modes[action])
        await state.set_data({'node_id':fields[2] if len(fields)>2 else None,'parts':[]})
        prompts={
            'add':'Отправь VLESS-ссылки (одна на строку, можно одним сообщением). /cancel — отмена.',
            'profile':'Отправь новое имя Vless config. /cancel — отмена.',
            'probe':'Отправь новый HTTPS URL проверки доступности. /cancel — отмена.',
            'rules':'Отправляй DIRECT-правила по одному на строку, можно несколькими сообщениями. '
                    '/done — сохранить все; /reset — очистить черновик; символ «-» очищает список. /cancel — отмена.',
            'editname':'Отправь новое имя выбранного сервера. /cancel — отмена.',
            'edituri':'Отправь новую VLESS-ссылку для выбранного сервера. /cancel — отмена.',
        }
        await callback.message.answer(_escape_auto(prompts[action]),parse_mode='HTML')
    elif action=='strategies':
        await _auto_render(callback,'⚖️ Выбери стратегию. leastPing — минимальная задержка.',
            _auto_keyboard([(f"{'✅ ' if s==st['strategy'] else ''}{s}",f'av:strategy:{s}') for s in vc.STRATEGIES]+
                           [('◀️ Назад','av:home')],1))
    elif action=='strategy' and len(fields)>=3 and fields[2] in vc.STRATEGIES:
        vc.update_settings(strategy=fields[2])
        await _auto_render(callback,'✅ Стратегия изменена; JSON обновлён',_vless_keyboard())
    elif action=='intervals':
        await _auto_render(callback,'⏱ Выбери период проверки серверов:',
            _auto_keyboard([(f"{'✅ ' if v==st['interval'] else ''}{v}",f'av:int:{v}') for v in vc.INTERVALS]+
                           [('◀️ Назад','av:home')],2))
    elif action=='int' and len(fields)>=3 and fields[2] in vc.INTERVALS:
        vc.update_settings(interval=fields[2])
        await _auto_render(callback,'✅ Интервал изменён; JSON обновлён',_vless_keyboard())
    else:
        await callback.answer('Устаревшее меню. Откройте /auto заново.',show_alert=True)
        return
    await callback.answer()


@dp.message(Command('cancel'), VlessBotState.waiting_links)
@dp.message(Command('cancel'), VlessBotState.waiting_profile_name)
@dp.message(Command('cancel'), VlessBotState.waiting_node_name)
@dp.message(Command('cancel'), VlessBotState.waiting_node_uri)
@dp.message(Command('cancel'), VlessBotState.waiting_probe)
@dp.message(Command('cancel'), VlessBotState.waiting_rules)
async def vless_bot_cancel(message: Message,state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer('Отменено.',reply_markup=_vless_keyboard())


@dp.message(Command('reset'), VlessBotState.waiting_rules)
async def vless_bot_reset(message: Message,state: FSMContext):
    if not is_admin(message.from_user.id):return
    await state.update_data(parts=[])
    await message.answer('Черновик DIRECT очищен. Присылай правила и нажми /done.')


@dp.message(Command('done'), VlessBotState.waiting_rules)
async def vless_bot_rules_done(message: Message,state: FSMContext):
    if not is_admin(message.from_user.id):return
    draft=await state.get_data()
    chunks=draft.get('parts',[])
    rules=[x.strip() for part in chunks for x in part.splitlines() if x.strip()]
    if rules==['-']:rules=[]
    try: vc.update_settings(direct_rules=rules)
    except ValueError as exc:
        await message.answer(_escape_auto(str(exc)),parse_mode='HTML');return
    await state.clear()
    await message.answer(f'✅ DIRECT-правила обновлены ({len(rules)})',reply_markup=_vless_keyboard())


@dp.message(VlessBotState.waiting_links)
@dp.message(VlessBotState.waiting_profile_name)
@dp.message(VlessBotState.waiting_node_name)
@dp.message(VlessBotState.waiting_node_uri)
@dp.message(VlessBotState.waiting_probe)
@dp.message(VlessBotState.waiting_rules)
async def vless_bot_input(message: Message,state: FSMContext):
    if not is_admin(message.from_user.id) or message.chat.type!='private':return
    value=(message.text or '').strip()
    mode=await state.get_state()
    if not value or value.startswith('/'):
        await message.answer('Пришли текст или /cancel');return
    if mode==VlessBotState.waiting_rules.state:
        draft=await state.get_data()
        parts=draft.get('parts',[])
        if sum(len(x) for x in parts)+len(value)>65000:
            await message.answer('Слишком много правил');return
        parts.append(value)
        await state.update_data(parts=parts)
        await message.answer(f'Принята часть {len(parts)}. /done — сохранить, /reset — начать заново.')
        return
    try:
        if mode==VlessBotState.waiting_links.state:
            vc.add_links(value)
            text='✅ Ключи добавлены; JSON обновлён.'
        elif mode==VlessBotState.waiting_profile_name.state:
            vc.update_settings(name=value)
            text='✅ Название Vless config обновлено.'
        elif mode==VlessBotState.waiting_probe.state:
            vc.update_settings(probe_url=value)
            text='✅ URL проверки обновлён.'
        else:
            draft=await state.get_data();ident=draft.get('node_id')
            if mode==VlessBotState.waiting_node_name.state:
                vc.update_node(ident,name=value);text='✅ Имя сервера изменено.'
            elif mode==VlessBotState.waiting_node_uri.state:
                vc.update_node(ident,uri=value);text='✅ VLESS-ключ заменён.'
            else:return
    except ValueError as exc:
        await message.answer('❌ '+_escape_auto(str(exc)),parse_mode='HTML');return
    await state.clear()
    await message.answer(text,reply_markup=_vless_keyboard())
