from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import db_driver
from datetime import datetime
from pathlib import Path

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

async def send_sub_page(request: Request, vpn_name, uuid):
    import xui_connection
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
            "additional_gb" : f"БС {used} из {total} ГБ." if data_rest else "",
        },
    )

