import os
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "/opt/BanEveryone/subserver.db")
def_size = int(os.getenv("def_size", "50"))
clear_trash = int(os.getenv("CLEAR_TRASH_EVERY_MIN", "60"))
sub = os.getenv("SUB", "sub")
XUI_SUB_URL = os.getenv("URL")
vpn_name = os.getenv("VPN", "Сосиски VPN")
start_at = int(os.getenv("PORTSERVER", 3322))
whiteList = os.getenv("WHITELIST", "").split(",")
XUI_BASE = os.getenv("XUI_BASE", "").rstrip("/")
XUI_TOKEN = os.getenv("XUI_TOKEN", "")
host = os.getenv("HOST", "main.service-ya.fun")

class user:
    def __init__(self, uuid='n/a', name='n/a', max_device='n/a', current_device=None, tg_id=0):
        self.uuid = uuid
        self.name = name
        self.max_device = max_device
        self.current_device = current_device if current_device is not None else []
        self.tg_id = tg_id

    def __str__(self):
        return (f'UUID: {self.uuid}\n'
                f'Name: {self.name}\n'
                f'Limit: {self.max_device}\n'
                f'Current devices: {len(self.current_device)}\n')

settings = {}
def_sett = {
    "update_users_interval": 1,
    "restricted_inbounds": [],
    "base_gb": 30 * 1024**3,
    "DB_PATH" : DB_PATH,
    "DEF_SIZE" : def_size,
    "CLEAR_TRASH" : clear_trash,
    "cost_per_month_full" : 200,
    "cost_per_month_base" : 150,
    "alert_days" : [1, 3]
}
most_important = {
    "sub" : sub,
    "XUI_SUB_URL" : XUI_SUB_URL,
    "vpn_name" : vpn_name,
    "start_at" : start_at,
    "whitelist" : whiteList,
    "XUI_BASE" : XUI_BASE,
    "XUI_TOKEN" : XUI_TOKEN,
    "HOST" : host,
}


def convert_to_user(uuid, limit, hwids, name):
    return user(uuid, name, limit, hwids)


def check_user_in_hwids(curr, userdata):
    if not userdata:
        return False
    for hwid in userdata:
        if hwid.get('hwid', "") == curr:
            return True
    return False
def user_update_hwid(curr, userdata):
    if not userdata:
        return userdata
    for i, hwid in enumerate(userdata):
        if hwid['hwid'] == curr['hwid']:
            userdata[i] = curr
            break
    return userdata
