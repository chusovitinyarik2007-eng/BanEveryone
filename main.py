import time
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
import userBot.bot as userbot

app = FastAPI()
app.mount("/static", StaticFiles(directory="web_subscription/static"), name="static")
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
    uuid = str(request.url).split('/')[-1]
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
        return await fetch_real_sub(uuid, request, 1, 100000)
    print(request.headers)
    hwid = request.headers.get('x-hwid') or request.headers.get("X-HWID")
    device_os = request.headers.get('x-device-os') or request.headers.get("X-DEVICE-OS")
    device = request.headers.get('x-device-model') or request.headers.get("X-DEVICE-MODEL")

    if not hwid:
        await tgbot.sendmsg_admin(f'Неопознанное устройство: {name}')
        return make_denied_response(UNCNOWN_DEVICE_BODY)

    if mx_devices <= len(device_info) and not classes.check_user_in_hwids(hwid, device_info):
        await tgbot.sendmsg_admin(f'Попытка повторного входа: {name}')
        return make_denied_response(DENIED_BODY)
    else:
        expiry = userinfo.get('client', {}).get('expiryTime', 0)
        if expiry > 0 and int(time.time() * 1000) > expiry  :
            return make_denied_response(EXPIRY_DEVICE_LINK)

        if classes.check_user_in_hwids(hwid, device_info):
            user.current_device = classes.user_update_hwid({
                "hwid" : hwid,
                "device_os" : device_os,
                "device_model" : device,
            }, device_info)
            db_driver.update_device_data_by(user)
            return await fetch_real_sub(uuid, request, mx_devices, len(device_info), totalgb, used_gb)
        else:
            user.current_device.append({
                "hwid" : hwid,
                "device_os" : device_os,
                "device_model" : device,
            })
            db_driver.update_device_data_by(user)
            return await fetch_real_sub(uuid, request, mx_devices, len(device_info), totalgb, used_gb)



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


async def fetch_real_sub(sub_id: str, request: Request, mx, cur, total_gb, remain_gb) -> Response:
    import tgbot
    url = settings["XUI_SUB_URL"].format(sub_id=sub_id)

    excluded_request_headers = {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
    }

    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded_request_headers
    }
    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        try:
            resp = await client.get(
                url,
                headers={
                    **forward_headers,
                },
            )
        except Exception as e:
            await tgbot.sendmsg_admin(f"Error: {e}")
            return make_denied_response(UNCNOWN_LINK)

    if resp.status_code != 200 or not resp.content:
        return make_denied_response(UNCNOWN_LINK)

    excluded_headers = {
        "content-length",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "upgrade",
    }

    out_headers = {
        key: value
        for key, value in resp.headers.items()
        if key.lower() not in excluded_headers
    }

    for h in (
        "Subscription-Userinfo",
        "Profile-Update-Interval",
        "Profile-Title",
        "Profile-Web-Page-Url",
        "Support-Url",
        "Announce",
    ):
        val = get_header_ci(resp.headers, h)
        if val:
            out_headers[h] = val

    # --- Announce: устройства ---
    original_announce = out_headers.get("Announce", "")
    if original_announce.startswith("base64:"):
        try:
            original_text = base64.b64decode(original_announce[7:]).decode("utf-8")
        except Exception:
            original_text = ""
    else:
        original_text = original_announce or ""

    new_text = f"📱Устройств: {cur}/{mx}\n{original_text}".strip()
    out_headers["Announce"] = "base64:" + base64.b64encode(new_text.encode("utf-8")).decode()

    # --- Subscription-Userinfo: трафик ---
    info = parse_userinfo(get_header_ci(resp.headers, "Subscription-Userinfo"))
    info["total"] = total_gb
    info["upload"] = 1
    info["download"] = remain_gb

    out_headers["Subscription-Userinfo"] = build_userinfo(info)

    return Response(content=resp.content, status_code=200, headers=out_headers)

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
        print("NOT BROWSER! HWID!")
        return False  # клиент VPN

    vpn_ua = ("happ", "v2ray", "clash", "sing-box", "shadowrocket",
              "nekobox", "stash", "quantumult", "surge", "loon")
    if any(x in ua for x in vpn_ua):
        print("NOT BROWSER! VPN CLIENT!")
        return False

    if "text/html" in accept:
        print("BROWSER!")
        return True

    # запас: типичный браузерный UA без hwid
    if any(x in ua for x in ("mozilla", "chrome", "safari", "firefox", "edg")):
        print("BROWSER!")
        return True
    print("NO ONE MATCH!")
    return False

async def run_bot():
    import tgbot
    while True:
        try:
            await tgbot.dp.start_polling(tgbot.bot)
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
    try:
        await server_task
    finally:
        bot_task.cancel()
        backup.cancel()
        trash.cancel()
        users_check_inbounds_rest.cancel()
        update_expire_user.cancel()
        userbot_task.cancel()
        for t in (bot_task, backup, trash, users_check_inbounds_rest, update_expire_user):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await tgbot.bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
