from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from starlette.requests import Request
import db_driver
import xui_connection
from datetime import datetime

templates = Jinja2Templates(directory="web_subscription/templates")

async def send_sub_page(request: Request, vpn_name, uuid):
    user = db_driver.get_data_by(uuid)
    hwids = user.current_device
    mx = user.max_device
    name = user.name
    data_all = (await xui_connection.get_user_info(name)).get("obj", dict())
    data_rest = (await xui_connection.get_user_info(name+"--rest"))
    data = data_all.get('client', None)
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
    expiry_time_ms = data["expiryTime"]
    expiry_timestamp = expiry_time_ms / 1000
    expiry_date = datetime.fromtimestamp(expiry_timestamp)
    current_date = datetime.now()
    time_left = expiry_date - current_date
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
            "date_expire" : expiry_date.strftime('%Y-%m-%d %H:%M:%S') if time_left.days > -20000 else "∞",
            "days" : time_left.days if time_left.days > -20000 else "∞",
            "current_usage_traffic" : current_traffic,
            "total_traffic" : total_traffic if total_traffic > 0 else "∞",
            "is_active" : isactive,
            "additional_gb" : f"БС {used} из {total} ГБ." if data_rest else "",
        },
    )

