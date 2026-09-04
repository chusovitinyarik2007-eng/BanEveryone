import asyncio
import warnings

from classes import settings
async def user_check_inbounds_rest():
    import xui_connection
    while True:
        users = await xui_connection.get_users_(False)
        what_to_remove = {}
        what_to_remove_extra = {}

        for email, user in users.items():
            curr_inb = user.get("id_inbounds", set())
            for i in curr_inb:
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
                user["group"] = ""
                inbound["obj"]["settings"]["clients"][index] = user
            await xui_connection.update_inbound(inb, inbound["obj"])

        for inb, email in what_to_remove_extra.items():
            inbound = await xui_connection.get_inbound(inb)
            if not inbound:
                continue
            index = 0
            while len(inbound["obj"]["settings"]["clients"]) > index:
                user = inbound["obj"]["settings"]["clients"][index]
                warnings.warn(f'Index {index}: {user["email"]}')
                if user["email"] not in email:
                    index += 1
                    continue
                inbound["obj"]["settings"]["clients"].pop(index)
            await xui_connection.update_inbound(inb, inbound["obj"])
        await asyncio.sleep(settings['update_users_interval'] * 60)

async def update_users_rest():
    import xui_connection
    await asyncio.sleep(5)
    while True:
        users = await xui_connection.get_users_(False)
        for email, user in users.items():
            if not email.endswith("--rest"):
                continue
            for email_sc, user_sc in users.items():
                if email == email_sc + "--rest":
                    user_upd = user
                    user_upd["enable"] = user_sc["enable"]
                    user_upd["group"] = ""
                    if user["expiryTime"] != user_sc["expiryTime"]:
                        user_upd["expiryTime"] = user_sc["expiryTime"]
                    user_upd["email"] = email
                    del user_upd["id_inbounds"]
                    await xui_connection.update_user(email, user_upd)
                    break

        await asyncio.sleep(60*60)