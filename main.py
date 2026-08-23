import asyncio
import base64
import os
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
import uvicorn
from backup_db import backup_schedule

import backup_db
import classes
import db_driver
import tgbot
from xui_connection import sync_names_with_panel

load_dotenv()
app = FastAPI()
sub = os.getenv("SUB", "sub")
XUI_SUB_URL = os.getenv("URL")
vpn_name = os.getenv("VPN", "Сосиски VPN")
start_at = int(os.getenv("PORTSERVER", 3322))
whiteList = os.getenv("WHITELIST", "").split(",")

DENIED_LINK = (
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Подписка%20уже%20активирована\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#Обратитесь%20к%20администратору\n"
    "vless://00000000-0000-0000-0000-000000000001@127.0.0.1:443"
    "?encryption=none&security=none&type=tcp#->%20@yar1k_ch"
)
DENIED_BODY = base64.b64encode(DENIED_LINK.encode()).decode()

@app.api_route("/"+sub+"/{uuid}", methods=["GET", "POST"])
async def get_sub(request: Request):
    uuid = str(request.url).split('/')[-1]

    user = db_driver.get_data_by(uuid)
    name = user.name
    mx_devices = user.max_device
    hwids = user.current_device

    if uuid in whiteList:
        return await fetch_real_sub(uuid, request, 1, 100000)
    hwid = request.headers.get('x-hwid') or request.headers.get("X-HWID")
    if not hwid:
        await tgbot.setmsg_admin(f'Неопознанное устройство: {name}')
        return make_denied_response()

    if mx_devices <= len(hwids) and hwid not in hwids:
        await tgbot.setmsg_admin(f'Попытка повторного входа: {name}')
        return make_denied_response()
    else:
        if hwid in hwids:
            return await fetch_real_sub(uuid, request, mx_devices, len(hwids))
        else:
            user.current_device.append(hwid)
            db_driver.update_device_data_by(user)
            return await fetch_real_sub(uuid, request, mx_devices, len(hwids))

async def fetch_real_sub(sub_id: str, request: Request, mx, cur) -> Response:
    url = XUI_SUB_URL.format(sub_id=sub_id)

    forward_headers = {}
    for h in ("user-agent", "accept", "accept-language"):
        if h in request.headers:
            forward_headers[h] = request.headers[h]

    async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
        try:
            resp = await client.get(
                url,
                headers={
                    **forward_headers,
                    "Host": "main.service-ya.fun",  # как в сертификате
                },
            )
        except Exception as e:
            await tgbot.setmsg_admin(f'Error: {e}')
            return {"ERROR" : str(e)}

    if resp.status_code != 200 or not resp.content:
        return {"ERROR" : "ERROR"}

    # Копируем важные заголовки от 3x-ui
    out_headers = {
        "Content-Type": resp.headers.get("content-type", "text/plain; charset=utf-8"),
    }
    for h in (
        "Subscription-Userinfo",
        "Profile-Update-Interval",
        "Profile-Title",
        "Profile-Web-Page-Url",
        "Support-Url",
        "Announce",
    ):
        if h in resp.headers:
            out_headers[h] = resp.headers[h]

    original_announce = out_headers.get("Announce", "")
    if original_announce.startswith("base64:"):
        try:
            original_text = base64.b64decode(original_announce[7:]).decode("utf-8")
        except Exception:
            original_text = original_announce
    else:
        original_text = original_announce
    new_text = f"📱Устройств: {cur}/{mx}\n{original_text}".strip()
    out_headers["Announce"] = "base64:" + base64.b64encode(new_text.encode("utf-8")).decode()
    return Response(content=resp.content, status_code=200, headers=out_headers)

def make_denied_response() -> Response:
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Profile-Title": "base64:" + base64.b64encode(vpn_name.encode()).decode(),
        "Profile-Update-Interval": "1",
        "Subscription-Userinfo": "upload=0; download=0; total=1; expire=0",
    }
    return Response(content=DENIED_BODY, media_type="text/plain", headers=headers)

async def run_bot():
    while True:
        try:
            await tgbot.dp.start_polling(tgbot.bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print("Bot error:", e)
            await asyncio.sleep(5)

async def main():
    db_driver.init_db()
    await sync_names_with_panel()

    config = uvicorn.Config(app, host="127.0.0.1", port=start_at, log_level="info")
    server = uvicorn.Server(config)

    bot_task = asyncio.create_task(run_bot())
    server_task = asyncio.create_task(server.serve())
    backup = asyncio.create_task(backup_schedule())

    try:
        await server_task
    finally:
        bot_task.cancel()
        backup.cancel()
        for t in (bot_task, backup):
            try:
                await t
            except asyncio.CancelledError:
                pass
        await tgbot.bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
