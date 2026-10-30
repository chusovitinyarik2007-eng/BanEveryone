import asyncio
import warnings

from classes import settings


async def user_check_inbounds_rest():
    """Keep manually-created panel clients in base + --rest form.

    If a normal client is manually attached to any restricted inbound, it is
    treated as a full/anti-block subscription: the normal client is moved to
    all regular inbounds and a real <email>--rest client is created/synced on
    all restricted inbounds.
    """
    import subscription_service
    import xui_connection

    while True:
        try:
            await xui_connection.sync_names_with_panel()
            users = await xui_connection.get_users_(False)
            restricted = {int(x) for x in settings.get("restricted_inbounds", [])}

            # Snapshot first: ensure_subscription_type_by_email mutates panel state.
            to_full = set()
            for email, user in list(users.items()):
                if email.endswith("--rest"):
                    base_email = email[:-6]
                    if base_email in users:
                        to_full.add(base_email)
                    continue

                current = {int(x) for x in (user.get("id_inbounds") or [])}
                if current & restricted:
                    to_full.add(email)

            for email in sorted(to_full):
                try:
                    await subscription_service.ensure_subscription_type_by_email(email, True)
                except Exception as exc:
                    warnings.warn(f"Не удалось синхронизировать {email}--rest: {exc}")

        except Exception as exc:
            warnings.warn(f"Ошибка фоновой синхронизации restricted inbound: {exc}")

        await asyncio.sleep(max(1, int(settings.get("update_users_interval", 1))) * 60)


async def update_users_rest():
    """Periodically synchronize expiry/enabled/tgId and restricted bindings."""
    import subscription_service
    import xui_connection

    await asyncio.sleep(5)
    while True:
        try:
            users = await xui_connection.get_users_(False)
            bases = {
                email[:-6]
                for email in users
                if email.endswith("--rest") and email[:-6] in users
            }
            for email in sorted(bases):
                try:
                    await subscription_service.ensure_subscription_type_by_email(email, True)
                except Exception as exc:
                    warnings.warn(f"Не удалось обновить {email}--rest: {exc}")
        except Exception as exc:
            warnings.warn(f"Ошибка синхронизации --rest: {exc}")

        await asyncio.sleep(60 * 60)
