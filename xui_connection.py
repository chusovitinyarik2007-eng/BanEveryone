import http
import json

import httpx

import db_driver
import os
from dotenv import load_dotenv

load_dotenv()
XUI_BASE = os.getenv("XUI_BASE", "").rstrip("/")
XUI_TOKEN = os.getenv("XUI_TOKEN", "")


async def get_users_uuid_names():
    users = {}
    headers = {
        "Authorization": f"Bearer {XUI_TOKEN}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.get(f"{XUI_BASE}/panel/api/inbounds/list", headers=headers)
        print("inbounds", r.status_code, r.text[:200])
        r.raise_for_status()
        data = r.json()
    if not data.get("success"):
        print("inbound list failed")
        return users

    for inb in data.get("obj") or []:
        sett = inb.get("settings")
        if isinstance(sett, str):
            sett = json.loads(sett)
        for cl in sett.get('clients') or []:
            uuid = (cl.get('subId') or "").strip()
            name = (cl.get('email') or "").strip()
            if uuid:
                users[uuid] = name or 'n/a'
    return users

async def sync_names_with_panel():
    currnt_users = db_driver.get_all_uuid()
    all_users = await get_users_uuid_names()
    for usr in currnt_users:
        name = all_users.get(usr, "n/a")
        db_driver.update_name_by_uuid(usr, name)
    for sub_id, email in all_users.items():
        db_driver.update_name_by_uuid(sub_id, email)
    print("Successfully sync names with panel")