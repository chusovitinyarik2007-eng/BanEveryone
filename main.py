import time
from pathlib import Path
import classes
import db_driver
db_driver.init_db()
from starlette.staticfiles import StaticFiles
from classes import settings
import asyncio
import httpx
from fastapi import FastAPI, Response
import uvicorn
from web_subscription.sub_page_loader import *
from backup_db import backup_schedule
from limited_inbounds import user_check_inbounds_rest, update_users_rest
import base64
import json
import userBot.bot as userbot

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web_subscription" / "static")), name="static")
DENIED_LINK = (
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Подписка%20уже%20активирована\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Обратитесь%20к%20администратору\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#->%20@yar1k_ch"
)
UNCNOWN_LINK = (
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Подписка%20не%20найдена\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Поддержка%20->%20@yar1k_ch"
)
UNCNOWN_DEVICE_LINK = (
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Обновите%20приложение\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#и%20подписка%20заработает\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Поддержка->%20@yar1k_ch"
)
EXPIRY_DEVICE_LINK = (
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Подписка%20закончилась\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Обратитесь%20к%20администратору\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Поддержка->%20@yar1k_ch"
)
DENIED_BODY = base64.b64encode(DENIED_LINK.encode()).decode()
UNCNOWN_BODY = base64.b64encode(UNCNOWN_LINK.encode()).decode()
UNCNOWN_DEVICE_BODY = base64.b64encode(UNCNOWN_DEVICE_LINK.encode()).decode()

@app.api_route("/"+settings["sub"]+"/{uuid}", methods=["GET", "POST"])
async def get_sub(request: Request):
    import tgbot
    import xui_connection
    uuid = str(request.path_params.get('uuid') or '').strip()
    if is_browser(request):
        return await send_sub_page(request, settings["vpn_name"], uuid)

    if not db_driver.is_user_exist(uuid):
        r = await xui_connection.sync_names_with_panel(uuid)
        if not r:
            return make_denied_response(UNCNOWN_LINK)

    user = db_driver.get_data_by(uuid)
    name = user.name
    mx_devices = user.max_device
    device_info = user.current_device
    userinfo = (await xui_connection.get_user_info(name)).get('obj', {})
    userinforest = await xui_connection.get_user_info(name + "--rest")
    used_gb = userinforest.get('obj', {}).get('usedTraffic', 0)
    totalgb = (userinforest.get('obj', {})
               .get('client', {})
               .get("totalGB", 0))
    if uuid in settings["whitelist"]:
        return await fetch_real_sub(uuid, request, mx_devices, len(device_info), totalgb, used_gb)
    hwid = request.headers.get('x-hwid') or request.headers.get("X-HWID")
    device_os = request.headers.get('x-device-os') or request.headers.get("X-DEVICE-OS")
    device = request.headers.get('x-device-model') or request.headers.get("X-DEVICE-MODEL")

    if not hwid:
        await tgbot.sendmsg_admin(f'Неопознанное устройство: {name}')
        return make_denied_response(UNCNOWN_DEVICE_BODY)

    expiry = userinfo.get('client', {}).get('expiryTime', 0)
    if expiry > 0 and int(time.time() * 1000) > expiry:
        return make_denied_response(EXPIRY_DEVICE_LINK)

    status, current_count, actual_limit = db_driver.register_or_refresh_device(
        uuid, hwid, device_os, device
    )
    if status == "limit":
        await tgbot.sendmsg_admin(f'Попытка повторного входа: {name}')
        return make_denied_response(DENIED_BODY)
    if status == "not_found":
        return make_denied_response(UNCNOWN_LINK)

    return await fetch_real_sub(
        uuid, request, actual_limit, current_count, totalgb, used_gb
    )



def parse_userinfo(raw: str) -> dict:
    out = {"upload": 0, "download": 0, "total": 0, "expire": 0}
    if not raw:
        return out
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        try:
            out[k] = int(float(v.strip()))
        except ValueError:
            pass
    return out


def build_userinfo(info: dict) -> str:
    return (
        f"upload={int(info.get('upload', 0))}; "
        f"download={int(info.get('download', 0))}; "
        f"total={int(info.get('total', 0))}; "
        f"expire={int(info.get('expire', 0))}"
    )


def get_header_ci(headers, name: str) -> str:
    name = name.lower()
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return ""


def _unique_lines(data: bytes) -> bytes:
    seen = set()
    out = []
    for raw in data.splitlines():
        line = raw.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        out.append(line)
    return b"\n".join(out) + (b"\n" if out else b"")


def _decode_subscription_base64(content: bytes):
    try:
        text = content.decode("utf-8").strip()
    except Exception:
        return None
    if not text:
        return None
    compact = "".join(text.split())
    compact += "=" * (-len(compact) % 4)
    try:
        decoded = base64.b64decode(compact, validate=True)
    except Exception:
        return None
    # Avoid treating arbitrary binary/text as a subscription payload.
    if b"://" not in decoded:
        return None
    return decoded


def _merge_subscription_content(primary: bytes, extra: bytes, content_type: str = "") -> bytes:
    """Merge base and --rest panel subscriptions into one public response.

    Happ and most V2Ray-style clients use either a base64 encoded URI list,
    a plain URI list, or a JSON array. These are merged structurally. The
    fallback is newline concatenation so unknown text formats still retain
    both payloads rather than silently dropping restricted connections.
    """
    if not extra:
        return primary

    # JSON subscription.
    if "json" in (content_type or "").lower() or primary.lstrip().startswith((b"[", b"{")):
        try:
            a = json.loads(primary.decode("utf-8"))
            b = json.loads(extra.decode("utf-8"))
            if isinstance(a, list) and isinstance(b, list):
                merged = a + [item for item in b if item not in a]
                return json.dumps(merged, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except Exception:
            pass

    # Standard base64 URI subscription.
    a64 = _decode_subscription_base64(primary)
    b64 = _decode_subscription_base64(extra)
    if a64 is not None and b64 is not None:
        return base64.b64encode(_unique_lines(a64 + b"\n" + b64))

    # Plain URI list.
    if b"://" in primary and b"://" in extra:
        return _unique_lines(primary + b"\n" + extra)

    return primary.rstrip() + b"\n" + extra.lstrip()

async def fetch_real_sub(
    sub_id: str,
    request: Request,
    mx,
    cur,
    total_gb,
    remain_gb,
) -> Response:
    import tgbot
    import subscription_service

    url = settings["XUI_SUB_URL"].format(
        sub_id=sub_id
    )

    rest_sub_id = await subscription_service.get_rest_sub_id(
        sub_id
    )

    rest_url = (
        settings["XUI_SUB_URL"].format(
            sub_id=rest_sub_id
        )
        if rest_sub_id
        else None
    )

    # ---------------------------------------------------------
    # REQUEST HEADERS
    # ---------------------------------------------------------

    excluded_request_headers = {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "accept-encoding",
    }

    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded_request_headers
    }

    # ---------------------------------------------------------
    # REQUEST TO 3X-UI
    # ---------------------------------------------------------

    async with httpx.AsyncClient(
        timeout=15.0,
        verify=False,
    ) as client:

        try:
            if rest_url:
                resp, rest_resp = await asyncio.gather(
                    client.get(
                        url,
                        headers=forward_headers,
                    ),
                    client.get(
                        rest_url,
                        headers=forward_headers,
                    ),
                )

            else:
                resp = await client.get(
                    url,
                    headers=forward_headers,
                )

                rest_resp = None

        except Exception as e:
            await tgbot.sendmsg_admin(
                f"Error: {e}"
            )

            return make_denied_response(
                UNCNOWN_LINK
            )

    # ---------------------------------------------------------
    # CHECK MAIN SUBSCRIPTION
    # ---------------------------------------------------------

    if (
        resp.status_code != 200
        or not resp.content
    ):
        return make_denied_response(
            UNCNOWN_LINK
        )

    # ---------------------------------------------------------
    # MERGE MAIN + --REST
    # ---------------------------------------------------------

    content = resp.content

    if (
        rest_resp is not None
        and rest_resp.status_code == 200
        and rest_resp.content
    ):
        content = _merge_subscription_content(
            resp.content,
            rest_resp.content,
            resp.headers.get(
                "content-type",
                "",
            ),
        )

    # ---------------------------------------------------------
    # RESPONSE HEADERS
    #
    # ВАЖНО:
    # Все ключи храним ТОЛЬКО В LOWERCASE.
    # ---------------------------------------------------------

    excluded_response_headers = {
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "upgrade",

        # Не нужно проксировать.
        # Uvicorn сформирует свои.
        "date",
        "server",
    }

    out_headers = {}

    for key, value in resp.headers.items():
        key = key.lower()

        if key in excluded_response_headers:
            continue

        out_headers[key] = value

    # ---------------------------------------------------------
    # ANNOUNCE
    # ---------------------------------------------------------

    original_announce = get_header_ci(
        resp.headers,
        "Announce",
    ) or ""

    if original_announce.startswith(
        "base64:"
    ):
        try:
            original_text = (
                base64.b64decode(
                    original_announce[7:]
                )
                .decode("utf-8")
            )

        except Exception:
            original_text = ""

    else:
        original_text = (
            original_announce
        )

    new_text = (
        f"📱Устройств: {cur}/{mx}\n"
        f"{original_text}"
    ).strip()

    out_headers["announce"] = (
        "base64:"
        + base64.b64encode(
            new_text.encode("utf-8")
        ).decode()
    )

    # ---------------------------------------------------------
    # SUBSCRIPTION USERINFO
    # ---------------------------------------------------------

    info = parse_userinfo(
        get_header_ci(
            resp.headers,
            "Subscription-Userinfo",
        )
    )

    info["total"] = total_gb
    info["upload"] = 1
    info["download"] = remain_gb

    out_headers[
        "subscription-userinfo"
    ] = build_userinfo(info)

    # ---------------------------------------------------------
    # RESPONSE
    # ---------------------------------------------------------

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=out_headers,
    )
def make_denied_response(info) -> Response:
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Title": "base64:" + base64.b64encode(settings["vpn_name"].encode()).decode(),
        "Profile-Update-Interval": "1",
        "Subscription-Userinfo": "upload=0; download=0; total=1; expire=0",
    }
    return Response(content=info, media_type="text/plain", headers=headers)

def is_browser(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    ua = (request.headers.get("user-agent") or "").lower()
    hwid = request.headers.get("x-hwid") or request.headers.get("X-HWID")

    if hwid:
        return False  # клиент VPN

    vpn_ua = ("happ", "v2ray", "clash", "sing-box", "shadowrocket",
              "nekobox", "stash", "quantumult", "surge", "loon")
    if any(x in ua for x in vpn_ua):
        return False

    if "text/html" in accept:
        return True

    # запас: типичный браузерный UA без hwid
    if any(x in ua for x in ("mozilla", "chrome", "safari", "firefox", "edg")):
        return True
    return False

async def run_bot():
    import tgbot
    while True:
        try:
            await tgbot.dp.start_polling(
                tgbot.bot,
                handle_signals=False,
            )
        except asyncio.CancelledError:
            break
        except Exception as e:
            print("Bot error:", e)
            await asyncio.sleep(5)



async def main():
    import xui_connection
    import tgbot
    await xui_connection.sync_names_with_panel()

    config = uvicorn.Config(app, host="0.0.0.0", port=settings["start_at"], log_level="info")
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(run_bot())
    server_task = asyncio.create_task(server.serve())
    backup = asyncio.create_task(backup_schedule())
    trash = asyncio.create_task(db_driver.clear_trash_loop())
    users_check_inbounds_rest = asyncio.create_task(user_check_inbounds_rest())
    update_expire_user = asyncio.create_task(update_users_rest())
    userbot_task = asyncio.create_task(userbot.run_bot())
    expiry_alert_task = asyncio.create_task(userbot.expiry_alert_loop())
    try:
        await server_task
    finally:
        bot_task.cancel()
        backup.cancel()
        trash.cancel()
        users_check_inbounds_rest.cancel()
        update_expire_user.cancel()
        userbot_task.cancel()
        expiry_alert_task.cancel()
        for t in (bot_task, backup, trash, users_check_inbounds_rest, update_expire_user, userbot_task, expiry_alert_task):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await tgbot.bot.session.close()
        await userbot.bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
