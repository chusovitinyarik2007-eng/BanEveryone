import json
import warnings
import httpx
from classes import settings
import db_driver


def _headers():
    return {
        "Authorization": f"Bearer {settings['XUI_TOKEN']}",
        "Accept": "application/json",
    }


async def _request(method: str, path: str, **kwargs):
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.request(
            method,
            f"{settings['XUI_BASE']}{path}",
            headers=_headers(),
            **kwargs,
        )
        r.raise_for_status()
        data = r.json()
    if not data.get("success", False):
        raise RuntimeError(data.get("msg") or f"3x-ui request failed: {path}")
    return data


async def get_user_info(name):
    try:
        return await _request("GET", f"/panel/api/clients/get/{name}")
    except Exception:
        return {}


async def get_user_info_strict(name: str):
    return await _request("GET", f"/panel/api/clients/get/{name}")


async def get_inbounds_options():
    try:
        data = await _request("GET", "/panel/api/inbounds/options")
        return data.get("obj") or []
    except Exception:
        data = await _request("GET", "/panel/api/inbounds/list")
        return [
            {
                "id": x.get("id"),
                "remark": x.get("remark", ""),
                "protocol": x.get("protocol", ""),
            }
            for x in data.get("obj") or []
        ]


async def create_client(client: dict, inbound_ids: list[int]):
    payload = dict(client or {})
    if "allowedIPs" in payload:
        payload["allowedIPs"] = _string_list(payload.get("allowedIPs"))
    if isinstance(payload.get("allowedIPsByInbound"), dict):
        payload["allowedIPsByInbound"] = {
            str(k): _string_list(v)
            for k, v in payload["allowedIPsByInbound"].items()
        }
    for key in (
        "traffic", "usedTraffic", "externalLinks", "tunnelAllowedIPs",
        "lastOnline", "lastSubFetch", "createdAt", "inboundIds",
    ):
        payload.pop(key, None)
    return await _request(
        "POST",
        "/panel/api/clients/add",
        json={"client": payload, "inboundIds": [int(x) for x in inbound_ids]},
    )


async def delete_client(email: str, keep_traffic: bool = False):
    suffix = "?keepTraffic=1" if keep_traffic else ""
    return await _request("POST", f"/panel/api/clients/del/{email}{suffix}")


async def attach_client(email: str, inbound_ids: list[int]):
    ids = sorted({int(x) for x in inbound_ids})
    if not ids:
        return {"success": True, "obj": None}
    return await _request(
        "POST",
        f"/panel/api/clients/{email}/attach",
        json={"inboundIds": ids},
    )


async def detach_client(email: str, inbound_ids: list[int]):
    ids = sorted({int(x) for x in inbound_ids})
    if not ids:
        return {"success": True, "obj": None}
    return await _request(
        "POST",
        f"/panel/api/clients/{email}/detach",
        json={"inboundIds": ids},
    )


def _string_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        return [x.strip() for x in value.split(",") if x.strip()]
    text = str(value).strip()
    return [text] if text else []


def _normalize_client_for_update(client: dict, email: str, inbound_ids: list[int]) -> dict:
    payload = dict(client or {})
    payload["email"] = str(payload.get("email") or email)
    payload["inboundIds"] = [int(x) for x in (inbound_ids or [])]

    if "allowedIPs" in payload:
        payload["allowedIPs"] = _string_list(payload.get("allowedIPs"))

    if isinstance(payload.get("allowedIPsByInbound"), dict):
        payload["allowedIPsByInbound"] = {
            str(k): _string_list(v)
            for k, v in payload["allowedIPsByInbound"].items()
        }

    # GET modern API may return numeric DB id. It is not the VPN credential.
    if isinstance(payload.get("id"), int):
        payload.pop("id", None)

    for key in (
        "traffic",
        "usedTraffic",
        "externalLinks",
        "tunnelAllowedIPs",
        "lastOnline",
        "lastSubFetch",
        "createdAt",
    ):
        payload.pop(key, None)

    return payload


async def update_client(email: str, client: dict, inbound_ids: list[int]):
    payload = _normalize_client_for_update(client, email, inbound_ids)
    return await _request(
        "POST",
        f"/panel/api/clients/update/{email}",
        json=payload,
    )


async def get_users_(onlu_uuid_names=True, update_tg_id=False) -> dict:
    users = {}
    data = await _request("GET", "/panel/api/inbounds/list")

    if not onlu_uuid_names:
        result = {}
        for inb in data.get("obj") or []:
            sett = inb.get("settings") or {}
            if isinstance(sett, str):
                try:
                    sett = json.loads(sett)
                except Exception:
                    sett = {}
            iid = inb.get("id")
            for cl in sett.get("clients") or []:
                email = (cl.get("email") or "").strip()
                if not email:
                    continue
                if email not in result:
                    result[email] = cl.copy()
                    result[email]["id_inbounds"] = []
                result[email]["id_inbounds"].append(iid)
        return result

    for inb in data.get("obj") or []:
        sett = inb.get("settings") or {}
        if isinstance(sett, str):
            try:
                sett = json.loads(sett)
            except Exception:
                sett = {}
        for cl in sett.get("clients") or []:
            sub_id = (cl.get("subId") or "").strip()
            email = (cl.get("email") or "").strip()
            tg_id = cl.get("tgId") or 0
            if email.endswith("--rest"):
                continue
            if sub_id:
                users[sub_id] = (
                    (email or "n/a", tg_id)
                    if update_tg_id
                    else (email or "n/a")
                )
    return users


async def sync_names_with_panel(uuid=None):
    all_users = await get_users_(update_tg_id=True)
    for sub_id, value in all_users.items():
        email, tg_id = value
        db_driver.update_name_by_uuid(sub_id, email, tg_id)
    if uuid is not None:
        return uuid in all_users
    return True


async def get_inbound(id):
    try:
        return await _request("GET", f"/panel/api/inbounds/get/{id}")
    except Exception as e:
        warnings.warn(f"Failed to get inbound {id}: {e}")
        return None


async def update_inbound(id, data):
    # Оставлено только для других старых админских сценариев. limited_inbounds
    # больше не меняет clients через этот endpoint.
    return await _request("POST", f"/panel/api/inbounds/update/{id}", json=data)


async def update_user(email, data):
    info = await get_user_info_strict(email)
    obj = info.get("obj") or {}
    inbound_ids = obj.get("inboundIds") or data.pop("id_inbounds", [])
    return await update_client(email, data, inbound_ids)

async def get_inbounds_full():
    """Get actual panel rows (options endpoint may omit edit fields)."""
    data = await _request("GET", "/panel/api/inbounds/list")
    return data.get("obj") or []
