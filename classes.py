import os
from dotenv import load_dotenv
import db_driver
db_driver.init_db()

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

class user:
    def __init__(self, uuid='n/a', name='n/a', max_device='n/a', current_device=[]):
        self.uuid = uuid
        self.name = name
        self.max_device = max_device
        self.current_device = current_device

    @property
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
    "sub" : sub,
    "XUI_SUB_URL" : XUI_SUB_URL,
    "vpn_name" : vpn_name,
    "start_at" : start_at,
    "whitelist" : whiteList,
    "XUI_BASE" : XUI_BASE,
    "XUI_TOKEN" : XUI_TOKEN,
}

def convert_to_user(uuid, limit, hwids, name):
    return user(uuid, name, limit, hwids)