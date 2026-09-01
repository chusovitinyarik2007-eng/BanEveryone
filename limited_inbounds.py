import asyncio
import warnings
from operator import index, truediv

import xui_connection
import backup_db

from classes import settings
async def user_check_inbounds_rest():
    while True:
        users = await xui_connection.get_users_(False)
        print(users)
        what_to_remove = {}
        what_to_remove_extra = {}

        for email, user in users.items():
            curr_inb = user.get("id_inbounds", set())
            print(f"working on {email}")
            for i in curr_inb:
                print(f"checking {email}; current inbounds: {curr_inb} (inbound {i}), res = {str(i) in settings['restricted_inbounds']}")
                if email.endswith("--rest"):
                    if str(i) not in settings['restricted_inbounds']:
                        what_to_remove_extra.setdefault(i, set()).add(email)
                elif str(i) in settings['restricted_inbounds']:
                    what_to_remove.setdefault(i, set()).add(email)
        for inb, email in what_to_remove.items():
            inbound = await xui_connection.get_inbound(inb)
            if not inbound:
                warnings.warn(f"Failed to get inbound for {inb}")
                continue
            for index, user in enumerate(inbound["obj"]["settings"]["clients"]):
                if user["email"] not in email:
                    continue
                user["email"] += "--rest"
                user["totalGB"] = settings['base_gb']
                user["trafficReset"] = "monthly"
                user["trafficResetDay"] = 1
                inbound["obj"]["settings"]["clients"][index] = user
            await xui_connection.update_inbound(inb, inbound["obj"])

        for inb, email in what_to_remove_extra.items():
            print(f"(remove extra) working on {email} with inbound id {inb}")
            inbound = await xui_connection.get_inbound(inb)
            if not inbound:
                print(f"(remove extra) Failed to get inbound for {inb}")
                warnings.warn(f"Failed to get inbound for {inb}")
                continue
            warnings.warn(f"(remove extra) TOTAL CLIENTS: {len(inbound["obj"]["settings"]["clients"])}")
            index = 0
            while len(inbound["obj"]["settings"]["clients"]) > index:
                user = inbound["obj"]["settings"]["clients"][index]
                warnings.warn(f'Index {index}: {user["email"]}')
                print(f"(remove extra) Checking {user['email']} with index {index}")
                if user["email"] not in email:
                    print(f"(remove extra) Skipping {user['email']}")
                    index += 1
                    continue
                inbound["obj"]["settings"]["clients"].pop(index)

            print(f"(remove extra) Updating {email} for {inb}")
            await xui_connection.update_inbound(inb, inbound["obj"])

        await asyncio.sleep(settings['update_users_interval'] * 60)

async def update_users_rest():
    await asyncio.sleep(5)
    while True:
        users = await xui_connection.get_users_(False)
        for email, user in users.items():
            if not email.endswith("--rest"):
                continue
            for email_sc, user_sc in users.items():
                print(f'{email} == {email_sc + "--rest"}, {email == email_sc + "--rest"}')
                if email == email_sc + "--rest":
                    user_upd = user
                    user_upd["enable"] = True
                    if user["expiryTime"] != user_sc["expiryTime"]:
                        user_upd["expiryTime"] = user_sc["expiryTime"]
                    user_upd["email"] = email
                    del user_upd["id_inbounds"]
                    await xui_connection.update_user(email, user_upd)
                    break

        await asyncio.sleep(60*60)