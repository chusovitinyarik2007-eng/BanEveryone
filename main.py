import asyncio
import base64
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
import uvicorn
import db_driver
import tgbot

load_dotenv()
app = FastAPI()
sub = os.getenv("SUB", "sub")
XUI_SUB_URL = os.getenv("URL")
vpn_name = os.getenv("VPN", "Сосиски VPN")
start_at = int(os.getenv("PORTSERVER", 3322))

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
    hwid = request.headers.get('x-hwid') or request.headers.get("X-HWID")
    if not hwid:
        return make_denied_response()

    mx_devices, hwids = db_driver.get_data_by_uuid(uuid)
    if mx_devices <= len(hwids) and hwid not in hwids:
        return make_denied_response()
    else:
        if hwid in hwids:
            return await fetch_real_sub(uuid, request, mx_devices, len(hwids))
        else:
            hwids.append(hwid)
            db_driver.update_device_data_by_uuid(uuid, hwids)
            return await fetch_real_sub(uuid, request, mx_devices, len(hwids))

async def fetch_real_sub(sub_id: str, request: Request, mx, cur) -> Response:
    url = XUI_SUB_URL.format(sub_id=sub_id)

    forward_headers = {}
    for h in ("user-agent", "accept", "accept-language"):
        if h in request.headers:
            forward_headers[h] = request.headers[h]

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=forward_headers)
        except Exception as e:
            return {"ERROR" : "ERROR"}

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
        except Exception as e:
            print("Bot error:", e)
            await asyncio.sleep(5)

async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=start_at, log_level="info")
    server = uvicorn.Server(config)
    await asyncio.gather(
        server.serve(),
        run_bot(),
    )

if __name__ == "__main__":
    asyncio.run(main())
