import http
import json
import warnings
import httpx
from classes import settings
import db_driver

async def get_user_info(name):
    headers = {
        "Authorization": f"Bearer {settings['XUI_TOKEN']}",
        "Accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
            r = await client.get(f"{settings['XUI_BASE']}/panel/api/clients/get/{name}", headers=headers)
            print("inbounds", r.status_code, r.text[:200])
            r.raise_for_status()
            data = r.json()
        if not data.get("success"):
            print("inbound list failed")
            return {}
        return data
    except Exception as e:
        return {}

async def get_users_(onlu_uuid_names = True) -> list | set:
    users = {}
    headers = {
        "Authorization": f"Bearer {settings['XUI_TOKEN']}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.get(f"{settings['XUI_BASE']}/panel/api/inbounds/list", headers=headers)
        print("inbounds", r.status_code, r.text)
        r.raise_for_status()
        data = r.json()
    if not data.get("success"):
        print("inbound list failed")
        return users
    if not onlu_uuid_names:
        user = {}
        for inb in data.get("obj") or []:
            sett = inb.get("settings")
            id_inb = inb.get("id")
            if isinstance(sett, str):
                sett = json.loads(sett)
            for cl in sett.get('clients') or []:
                email = (cl.get('email') or "").strip()
                if email:
                    if email not in user:
                        user[email] = cl.copy()
                    if not user[email].get("id_inbounds", None):
                        user[email]['id_inbounds'] = []
                    user[email]["id_inbounds"].append(id_inb)
        return user

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

async def sync_names_with_panel(uuid = None):
    currnt_users = db_driver.get_all_uuid()
    all_users = await get_users_()
    for usr in currnt_users:
        name = all_users.get(usr, "n/a")
        if name.endswith("--rest"):
            continue
        db_driver.update_name_by_uuid(usr, name)
    for sub_id, email in all_users.items():
        if email.endswith("--rest"):
            continue
        db_driver.update_name_by_uuid(sub_id, email)
        if uuid == sub_id:
            return True
    print("Successfully sync names with panel")
    if uuid is not None:
        return False

async def get_inbound(id):
    headers = {
        "Authorization": f"Bearer {settings['XUI_TOKEN']}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.get(f"{settings['XUI_BASE']}/panel/api/inbounds/get/{id}", headers=headers)
        print(f"inbound {id} --> ", r.status_code, r.text)
        r.raise_for_status()
        if r.status_code == 200:
            data = r.json()
            return data
        else:
            warnings.warn(f"Failed to get inbound {id}, error: {r.status_code}")
        return None

async def update_inbound(id, data):
    headers = {
        "Authorization": f"Bearer {settings['XUI_TOKEN']}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.post(f"{settings['XUI_BASE']}/panel/api/inbounds/update/{id}", headers=headers, json=data)
        r.raise_for_status()
        if r.status_code != 200:
            warnings.warn(f"Failed to update inbound {id}, error: {r.status_code}")

        print("inbounds", r.status_code, r.text)
        data = r.json()
        if not data.get("success"):
            warnings.warn(f"Failed to update inbound {id}, error: {r.status_code}, \n{data}")

async def update_user(email, data):
    headers = {
        "Authorization": f"Bearer {settings['XUI_TOKEN']}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(verify=False, timeout=20.0) as client:
        r = await client.post(f"{settings['XUI_BASE']}/panel/api/clients/update/{email}", headers=headers, json=data)
        r.raise_for_status()
        if r.status_code != 200:
            warnings.warn(f"Failed to update user {email}, error: {r.status_code}, \nText error: {r.text}")

        data = r.json()
        warnings.warn(f"Failed to update user {email}, error: {r.status_code}, \n{data}")
