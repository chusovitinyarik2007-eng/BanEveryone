from urllib.parse import urlparse, unquote, parse_qs

import httpx
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import dotenv
import db_driver
from datetime import datetime
from pathlib import Path
import os
import base64

dotenv.load_dotenv()

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
base_usr_for_keys = os.getenv("BASE_SUB_URL")

def parse_vless(key):
    parsed = urlparse(key)
    protocol = parsed.scheme
    name = unquote(parsed.fragment) if parsed.fragment else "Без имени"
    query_params = parse_qs(parsed.query)
    transport = query_params.get("type", ["tcp"])[
        0
    ]
    uri_full = key
    return name, protocol, transport, uri_full


def decode(base64text):
    decoded_bytes = base64.b64decode(base64text)
    return decoded_bytes.decode("utf-8")

async def _request(usr_id):
    print(base_usr_for_keys.format(sub_id = usr_id))
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.request(
            "GET",
            base_usr_for_keys.format(sub_id = usr_id)
        )
        r.raise_for_status()
        data = r.text
    return data

async def _get_keys(base_usr, rest_usr = None):
    base_usr_conn = ""
    rest_usr_conn = ""
    if not base_usr:
        raise Exception("User is None")
    base_usr_conn = await _request(base_usr.get("subId", ""))
    base_usr_conn = decode(base_usr_conn)
    if rest_usr:
        rest_usr_conn = await _request(rest_usr.get("subId", ""))
        rest_usr_conn = decode(rest_usr_conn)
        rest_usr_conn = rest_usr_conn.split("\n")
    base_usr_conn = base_usr_conn.split('\n')
    return base_usr_conn, rest_usr_conn



async def send_sub_page(request: Request, vpn_name, uuid):
    import xui_connection
    user = db_driver.get_data_by(uuid)
    hwids = user.current_device
    mx = user.max_device
    name = user.name
    data_all = (await xui_connection.get_user_info(name)).get("obj", dict())
    data_rest = (await xui_connection.get_user_info(name+"--rest"))
    all_usr_keys, rest_user_keys = await _get_keys(data_all.get('client', None),
                                                   data_rest.get("obj", dict()).get('client', None))
    data = data_all.get('client', None)
    keys_visible = True if data.get("expiryTime", 1) == 0 else False
    keys = []
    if keys_visible:
        for i in all_usr_keys:
            name, protocol, transport, uri_full = parse_vless(i)
            if not i:
                continue
            keys.append({
                "name" : name,
                "protocol" : protocol,
                "transport" : transport,
                "uri" : uri_full,
                "is_reserve" : False
            })
        for i in rest_user_keys:
            name, protocol, transport, uri_full = parse_vless(i)
            if not i:
                continue
            keys.append({
                "name" : name,
                "protocol" : protocol,
                "transport" : transport,
                "uri" : uri_full,
                "is_reserve" : True
            })

    if not data:
        return {"EROOR" : "NO SUCH USER FOUND"}
    total = 0
    used = 0
    if data_rest:
        total = round(int(data_rest.get('obj', {})
               .get('client', {})
               .get("totalGB", 0)) / 1024 ** 3, 2)
        used = round(data_rest.get('obj', {}).get("usedTraffic", 0) / 1024**3, 2)
    total = "∞" if total == 0 else total
    expiry_time_ms = int(data.get("expiryTime") or 0)
    current_date = datetime.now()
    if expiry_time_ms > 0:
        expiry_date = datetime.fromtimestamp(expiry_time_ms / 1000)
        time_left = expiry_date - current_date
        date_expire = expiry_date.strftime('%Y-%m-%d %H:%M:%S')
        days_left = time_left.days
    else:
        expiry_date = None
        date_expire = "∞"
        days_left = "∞"
    current_traffic = round(data_all["usedTraffic"]/1024/1024/1024, 2)
    total_traffic = int(data["totalGB"])/1024/1024//1024
    isactive = data["enable"]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": vpn_name,
            "uuid": uuid,
            "name": name,
            "current_devices": len(hwids) if hwids else 0,
            "max_devices": mx,
            "date_expire" : date_expire,
            "days" : days_left,
            "current_usage_traffic" : current_traffic,
            "total_traffic" : total_traffic if total_traffic > 0 else "∞",
            "is_active" : isactive,
            "rest_traffic": {
                "used_gb": used,
                "total_gb": total
            } if data_rest else None,
            "keys_visible": keys_visible,
            "connection_keys": keys
        },
    )

