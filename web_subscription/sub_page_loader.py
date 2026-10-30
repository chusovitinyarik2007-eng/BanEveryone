"""User-facing subscription page. Never fetch or expose connection URIs unless allowed."""
import base64
import logging
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from fastapi import HTTPException
from fastapi.templating import Jinja2Templates
from pathlib import Path

import db_driver
import xui_connection

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def parse_vless(key):
    parsed = urlparse(key.strip())
    return (
        unquote(parsed.fragment) if parsed.fragment else "Без имени",
        parsed.scheme.lower(),
        parse_qs(parsed.query).get("type", ["tcp"])[0].lower(),
        key.strip(),
    )


def decode(text):
    """Decode a subscription base64 payload, tolerating omitted padding."""
    payload = "".join(str(text).split())
    payload += "=" * (-len(payload) % 4)
    return base64.b64decode(payload, validate=True).decode("utf-8")


async def _request(sub_id):
    template = os.getenv("BASE_SUB_URL", "")
    if not template:
        raise RuntimeError("BASE_SUB_URL is not set")
    url = template.format(sub_id=sub_id)
    # CA validation is enabled by default. For private CAs use a custom trust store.
    async with httpx.AsyncClient(timeout=12.0, verify=False) as client:
        response = await client.get(url, headers={"Accept": "text/plain"})
        response.raise_for_status()
        return response.text


async def _get_keys(base_user, rest_user=None):
    if not isinstance(base_user, dict) or not base_user.get("subId"):
        return [], []
    primary = decode(await _request(base_user["subId"])).splitlines()
    reserve = []
    if isinstance(rest_user, dict) and rest_user.get("subId"):
        reserve = decode(await _request(rest_user["subId"])).splitlines()
    return primary, reserve


async def send_sub_page(request, vpn_name, uuid):
    user = db_driver.get_data_by(uuid)
    if not user or user.name == "n/a":
        raise HTTPException(status_code=404, detail="Подписка не найдена")

    base_obj = (await xui_connection.get_user_info(user.name)).get("obj") or {}
    data = base_obj.get("client") or {}
    if not data:
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    rest_obj = (await xui_connection.get_user_info(user.name + "--rest")).get("obj") or {}
    rest_client = rest_obj.get("client") or None

    # Existing policy: connection URIs are visible for unlimited subscriptions only.
    # Do not even retrieve URIs for ordinary subscriptions.
    keys_visible = int(data.get("expiryTime") or 1) == 0
    keys = []
    if keys_visible:
        try:
            primary, reserve = await _get_keys(data, rest_client)
            for is_reserve, group in ((False, primary), (True, reserve)):
                for uri in group:
                    if not uri.strip():
                        continue
                    name, protocol, transport, full = parse_vless(uri)
                    if protocol not in {"vless", "vmess", "trojan", "ss", "hysteria2", "hy2"}:
                        continue
                    keys.append({"name": name, "protocol": protocol,
                                 "transport": transport, "uri": full,
                                 "is_reserve": is_reserve})
        except (httpx.HTTPError, ValueError, UnicodeError, RuntimeError):
            logger.exception("Unable to load keys for subscription page")
            keys = []  # The page still works when the key endpoint is unavailable.

    expiry_ms = int(data.get("expiryTime") or 0)
    now = datetime.now(timezone.utc)
    if expiry_ms > 0:
        expiry = datetime.fromtimestamp(expiry_ms / 1000, tz=timezone.utc)
        date_expire = expiry.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        days_left = max(0, (expiry - now).days)
    else:
        date_expire, days_left = "∞", "∞"

    def gigabytes(value):
        return round(float(value or 0) / (1024 ** 3), 2)

    used = gigabytes(base_obj.get("usedTraffic"))
    total = gigabytes(data.get("totalGB"))
    rest_traffic = None
    if rest_client:
        reserve_total = gigabytes(rest_client.get("totalGB"))
        rest_traffic = {"used_gb": gigabytes(rest_obj.get("usedTraffic")),
                        "total_gb": reserve_total if reserve_total > 0 else "∞"}

    return templates.TemplateResponse(
        request=request, name="index.html", context={
            "title": vpn_name, "uuid": uuid, "name": user.name,
            "current_devices": len(user.current_device or []),
            "max_devices": user.max_device,
            "date_expire": date_expire, "days": days_left,
            "current_usage_traffic": used,
            "total_traffic": total if total > 0 else "∞",
            "is_active": bool(data.get("enable", False)) and (not expiry_ms or expiry_ms > int(now.timestamp() * 1000)),
            "rest_traffic": rest_traffic,
            "keys_visible": keys_visible, "connection_keys": keys,
        },
    )
