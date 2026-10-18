import calendar
import secrets
import string
import uuid as uuidlib
from datetime import datetime

import db_driver
import xui_connection
from classes import settings

PERIODS = {1: "1 месяц", 3: "3 месяца", 6: "6 месяцев", 12: "1 год"}


def monthly_price(anti_block: bool) -> int:
    key = "cost_per_month_full" if anti_block else "cost_per_month_base"
    return int(settings.get(key, 0))


def price_for_months(months: int, anti_block: bool = False, count: int = 1, monthly_cost: int | None = None) -> int:
    cost = int(monthly_cost) if monthly_cost is not None else monthly_price(anti_block)
    return cost * int(months) * int(count)


def format_price(value: int) -> str:
    return f"{int(value):,}".replace(",", " ") + " ₽"


def add_months_ms(timestamp_ms: int, months: int) -> int:
    now = datetime.now()
    # Бессрочная подписка (expiry=0) при продлении получает обычный срок
    # от текущего момента.
    base = (
        datetime.fromtimestamp(timestamp_ms / 1000)
        if timestamp_ms and timestamp_ms > int(now.timestamp() * 1000)
        else now
    )
    month0 = base.month - 1 + months
    year = base.year + month0 // 12
    month = month0 % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return int(base.replace(year=year, month=month, day=day).timestamp() * 1000)


def random_name(username: str | None) -> str:
    suffix = (username or "user").strip().lstrip("@") or "user"
    suffix = "".join(ch for ch in suffix if ch.isalnum() or ch in "_-")[:24] or "user"
    prefix = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"{prefix}_{suffix}"


async def list_inbounds():
    """Только пользовательские inbound'ы. HTTP — служебные и исключаются."""
    inbounds = await xui_connection.get_inbounds_options()
    return [
        inbound
        for inbound in inbounds
        if str(inbound.get("protocol") or "").strip().lower() != "http"
    ]


async def _inbound_groups():
    inbounds = await list_inbounds()
    valid = {int(x["id"]) for x in inbounds if x.get("id") is not None}
    restricted_cfg = {int(x) for x in settings.get("restricted_inbounds", [])}
    restricted = valid & restricted_cfg
    regular = valid - restricted
    return inbounds, regular, restricted


def _credential(client: dict) -> str:
    value = client.get("uuid")
    if value:
        return str(value)
    value = client.get("id")
    if isinstance(value, str) and value:
        return value
    return str(uuidlib.uuid4())


def _rest_client_from_base(base_client: dict, rest_email: str, existing_rest: dict | None = None) -> dict:
    """Build the technical --rest client from the main client.

    All connection credentials are copied from the main client (uuid/id,
    password, auth, secret, keys, etc.).  Only the email and subId identity of
    the technical client differ.  For a *new* --rest client subId is omitted so
    3x-ui generates a unique value itself.  For an existing new-format client
    its already-generated subId is preserved.
    """
    rest = dict(base_client or {})
    existing_rest = dict(existing_rest or {})

    for key in (
        "id_inbounds", "inboundIds", "usedTraffic", "traffic",
        "lastOnline", "lastSubFetch", "createdAt", "updatedAt",
    ):
        rest.pop(key, None)

    rest["email"] = rest_email

    # Modern GET responses can expose an internal numeric row id separately
    # from the actual VPN UUID.  For the technical client we need the same VPN
    # identity as the base client, never the numeric database id.
    base_uuid = str(base_client.get("uuid") or "").strip()
    if not base_uuid:
        raw_id = base_client.get("id")
        if isinstance(raw_id, str):
            base_uuid = raw_id.strip()
    if base_uuid:
        rest["uuid"] = base_uuid
        rest["id"] = base_uuid
    elif isinstance(rest.get("id"), int):
        rest.pop("id", None)

    # Important: do not invent subId here.  When creating a new technical
    # client the field is omitted and 3x-ui generates it.
    existing_sub_id = str(existing_rest.get("subId") or "").strip()
    if existing_sub_id:
        rest["subId"] = existing_sub_id
    else:
        rest.pop("subId", None)

    # Keep the old service-client traffic policy, but do not touch any
    # protocol credential copied from the base client.
    rest["totalGB"] = int(settings.get("base_gb", 0))
    rest["trafficReset"] = "monthly"
    rest["trafficResetDay"] = 1
    rest["group"] = ""
    rest["enable"] = bool(base_client.get("enable", True))
    rest["expiryTime"] = int(base_client.get("expiryTime") or 0)
    rest["tgId"] = int(base_client.get("tgId") or 0)
    rest.setdefault("limitIp", 0)
    return rest


def _same_sub_id(a: dict | None, b: dict | None) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    av = str(a.get("subId") or "").strip()
    bv = str(b.get("subId") or "").strip()
    return bool(av and bv and av == bv)


async def _get_client_obj(email: str, strict: bool = False):
    data = (
        await xui_connection.get_user_info_strict(email)
        if strict
        else await xui_connection.get_user_info(email)
    )
    obj = (data or {}).get("obj") or {}
    client = obj.get("client")
    if not isinstance(client, dict):
        return None
    return {"client": client, "inboundIds": obj.get("inboundIds") or []}


async def get_rest_sub_id(sub_id: str) -> str | None:
    user = db_driver.get_data_by(sub_id)
    if not user or user.name == "n/a":
        return None
    obj = await _get_client_obj(user.name + "--rest", strict=False)
    if not obj:
        return None
    value = str(obj["client"].get("subId") or "").strip()
    return value or None


async def ensure_subscription_type_by_email(base_email: str, full: bool):
    """Synchronize a subscription to the base + --rest model.

    Compatibility rules:
    * New format: --rest is a real Client API record with a panel-generated
      unique subId, but all protocol credentials are identical to the base.
    * Legacy format: if base and --rest have the same subId, the technical
      client is converted once by deleting only the old --rest record and
      recreating it with the same credentials while letting 3x-ui generate a
      new subId.  The base client, its subId and public URL never change.
    """
    _, regular_ids, restricted_ids = await _inbound_groups()
    base_obj = await _get_client_obj(base_email, strict=True)
    if not base_obj:
        raise RuntimeError("Основной клиент не найден")

    base_client = dict(base_obj["client"])
    base_attached = {int(x) for x in (base_obj.get("inboundIds") or [])}
    rest_email = base_email + "--rest"

    # get_users_(False) sees legacy --rest entries that can still exist inside
    # inbound settings even when the newer per-client endpoint behaves
    # differently after a panel upgrade.
    panel_snapshot = await xui_connection.get_users_(False)
    legacy_snapshot = dict(panel_snapshot.get(rest_email) or {})
    rest_obj = await _get_client_obj(rest_email, strict=False)
    rest_client_current = dict(rest_obj["client"]) if rest_obj else legacy_snapshot

    # The main client always belongs only to ordinary user inbounds.
    missing_regular = set(regular_ids) - base_attached
    restricted_on_base = base_attached & set(restricted_ids)
    # if missing_regular:
    #     await xui_connection.attach_client(base_email, sorted(missing_regular)) # убрать эту хуйню!!!
    if restricted_on_base:
        await xui_connection.detach_client(base_email, sorted(restricted_on_base))

    if not full:
        if rest_obj or legacy_snapshot:
            try:
                await xui_connection.delete_client(rest_email)
            except Exception:
                # If the old panel record is visible only in inbound settings,
                # do not create a replacement.  Surface the problem instead of
                # producing a duplicate technical client.
                if legacy_snapshot:
                    raise
        return False

    if not restricted_ids:
        if rest_obj or legacy_snapshot:
            try:
                await xui_connection.delete_client(rest_email)
            except Exception:
                pass
        return True

    # One-time migration from the old format where base and --rest shared the
    # same subId.  Credentials are deliberately kept identical, therefore old
    # imported VPN configurations continue to work after the conversion.
    legacy_same_subid = _same_sub_id(base_client, rest_client_current)
    if legacy_same_subid:
        try:
            await xui_connection.delete_client(rest_email, keep_traffic=True)
        except Exception as exc:
            raise RuntimeError(
                f"Не удалось преобразовать старый технический клиент {rest_email}: {exc}"
            ) from exc
        rest_obj = None
        rest_client_current = {}

    if not rest_obj:
        # Do not provide subId: current 3x-ui generates a unique value.
        rest_client = _rest_client_from_base(base_client, rest_email)
        await xui_connection.create_client(rest_client, sorted(restricted_ids))
        rest_obj = await _get_client_obj(rest_email, strict=True)
        if not rest_obj:
            raise RuntimeError("Панель не вернула созданный технический клиент")
        generated_sub_id = str(rest_obj["client"].get("subId") or "").strip()
        if not generated_sub_id or generated_sub_id == str(base_client.get("subId") or "").strip():
            raise RuntimeError("Панель не сгенерировала отдельный subId для технического клиента")
        if _credential(rest_obj["client"]) != _credential(base_client):
            raise RuntimeError("Технический клиент создан с другими данными подключения")
    else:
        existing = dict(rest_obj["client"])
        rest_client = _rest_client_from_base(base_client, rest_email, existing)
        await xui_connection.update_client(
            rest_email, rest_client, rest_obj.get("inboundIds") or []
        )

        rest_attached = {int(x) for x in (rest_obj.get("inboundIds") or [])}
        missing = set(restricted_ids) - rest_attached
        extra = rest_attached - set(restricted_ids)
        if missing:
            await xui_connection.attach_client(rest_email, sorted(missing))
        if extra:
            await xui_connection.detach_client(rest_email, sorted(extra))

    return True


async def set_subscription_type(sub_id: str, full: bool):
    user = db_driver.get_data_by(sub_id)
    if not user or user.name == "n/a":
        raise RuntimeError("Подписка не найдена")
    return await ensure_subscription_type_by_email(user.name, full)


async def create_subscription(tg_id: int, username: str | None, months: int, anti_block: bool):
    _, regular_ids, restricted_ids = await _inbound_groups()
    if not regular_ids:
        raise RuntimeError("Нет доступных обычных подключений для создания подписки")

    email = random_name(username)
    sub_id = uuidlib.uuid4().hex
    expiry = add_months_ms(0, months)
    credential = str(uuidlib.uuid4())
    client = {
        "email": email,
        "subId": sub_id,
        "id": credential,
        "uuid": credential,
        "totalGB": 0,
        "expiryTime": expiry,
        "tgId": int(tg_id),
        "limitIp": 0,
        "enable": True,
    }
    # Основной клиент всегда создаётся только на обычных inbound.
    # Для full технический --rest создаётся отдельно с уникальным внутренним
    # subId и привязывается ко всем restricted inbound.
    await xui_connection.create_client(client, sorted(regular_ids))

    try:
        if anti_block and restricted_ids:
            await ensure_subscription_type_by_email(email, True)
    except Exception:
        # Если часть restricted inbound уже успела превратиться в --rest,
        # сначала убираем технические копии, затем удаляем основной клиент.
        try:
            await ensure_subscription_type_by_email(email, False)
        except Exception:
            pass
        try:
            await xui_connection.delete_client(email)
        except Exception:
            pass
        raise

    db_driver.update_name_by_uuid(sub_id, email, tg_id)
    db_driver.set_subscription_max_device(sub_id, 2)
    return sub_id, email, sorted(regular_ids | (restricted_ids if anti_block else set())), expiry


async def rotate_subscription_credentials(sub_id: str):
    user = db_driver.get_data_by(sub_id)
    if not user or user.name == "n/a":
        raise RuntimeError("Подписка не найдена")

    base_obj = await _get_client_obj(user.name, strict=True)
    if not base_obj:
        raise RuntimeError("Подписка не найдена")

    # Rotate the main identity once.  The --rest client must remain a copy of
    # the main client, so it receives exactly the same protocol credentials.
    fresh_uuid = str(uuidlib.uuid4())
    base_client = dict(base_obj["client"])
    base_client["email"] = str(base_client.get("email") or user.name)
    base_client["id"] = fresh_uuid
    base_client["uuid"] = fresh_uuid
    await xui_connection.update_client(
        user.name, base_client, base_obj.get("inboundIds") or []
    )

    verify_base = await _get_client_obj(user.name, strict=True)
    if not verify_base or _credential(verify_base["client"]) != fresh_uuid:
        raise RuntimeError("Не удалось обновить данные подключения")

    changed = [user.name]
    rest_email = user.name + "--rest"
    rest_obj = await _get_client_obj(rest_email, strict=False)
    if rest_obj:
        # Rebuild from the verified main client, preserving only rest's unique
        # subId and service traffic policy.  This synchronizes password/auth/
        # secret/keys as well as uuid/id.
        rest_client = _rest_client_from_base(
            dict(verify_base["client"]), rest_email, dict(rest_obj["client"])
        )
        await xui_connection.update_client(
            rest_email, rest_client, rest_obj.get("inboundIds") or []
        )
        verify_rest = await _get_client_obj(rest_email, strict=True)
        if not verify_rest or _credential(verify_rest["client"]) != fresh_uuid:
            raise RuntimeError("Не удалось обновить данные технического подключения")
        changed.append(rest_email)

    return changed


async def remove_device(sub_id: str, hwid: str, tg_id: int):
    user = db_driver.get_owned_subscription(sub_id, tg_id)
    if not user:
        raise PermissionError("Подписка не принадлежит пользователю")
    if not any(str(d.get("hwid")) == str(hwid) for d in (user.current_device or [])):
        raise RuntimeError("Устройство не найдено")
    await rotate_subscription_credentials(sub_id)
    if not db_driver.remove_single_hwid(sub_id, hwid):
        raise RuntimeError("Не удалось обновить список устройств")


async def detach_subscription(sub_id: str, tg_id: int):
    user = db_driver.get_owned_subscription(sub_id, tg_id)
    if not user:
        raise PermissionError("Подписка не принадлежит пользователю")

    for index, email in enumerate((user.name, user.name + "--rest")):
        obj = await _get_client_obj(email, strict=(index == 0))
        if not obj:
            continue
        client = dict(obj["client"])
        client["tgId"] = 0
        await xui_connection.update_client(email, client, obj.get("inboundIds") or [])
    db_driver.set_subscription_tg_id(sub_id, 0)


async def renew_subscriptions(sub_ids: list[str], tg_id: int, months: int = 1):
    """Продлевает только подписки с конечным сроком действия.

    expiryTime <= 0 считается бессрочной подпиской. Она никогда не получает
    срок через бота, даже если её subId оказался в старой/ручной заявке.
    """
    renewed = []
    for sub_id in sub_ids:
        user = db_driver.get_owned_subscription(sub_id, tg_id)
        if not user:
            continue
        base_obj = await _get_client_obj(user.name, strict=True)
        if not base_obj:
            continue

        current_expiry = int(base_obj["client"].get("expiryTime") or 0)
        if current_expiry <= 0:
            continue

        new_expiry = add_months_ms(current_expiry, months)

        for index, email in enumerate((user.name, user.name + "--rest")):
            obj = base_obj if index == 0 else await _get_client_obj(email, strict=False)
            if not obj:
                continue
            client = dict(obj["client"])
            client["expiryTime"] = new_expiry
            await xui_connection.update_client(email, client, obj.get("inboundIds") or [])
        renewed.append(sub_id)
    return renewed


def public_subscription_url(sub_id: str) -> str:
    host = str(settings.get("HOST", "")).strip().rstrip("/")
    scheme = "https://" if not host.startswith(("http://", "https://")) else ""
    return f"{scheme}{host}/{settings['sub'].strip('/')}/{sub_id}"


async def set_subscription_device_limit(sub_id: str, limit: int):
    user = db_driver.get_data_by(sub_id)
    if not user or user.name == "n/a":
        raise RuntimeError("Подписка не найдена")
    limit = int(limit)
    if limit < 0:
        raise ValueError("Лимит не может быть отрицательным")
    db_driver.set_subscription_max_device(user.uuid, limit)
    return db_driver.get_data_by(user.uuid)
