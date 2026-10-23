"""Telegram buttons on AI failure alerts: pause or stop the printer from the chat.

Bambuddy otherwise only sends to Telegram. This service long-polls getUpdates
for button presses (callback queries) on every enabled Telegram provider that
receives AI failure alerts. A press is honoured only from that provider's own
chat, and only while the printer still runs the print the alert was about: the
button carries a fingerprint of the job name, so an old alert can never stop
the next print on the same printer.

Stop sends no confirmation question; that was the explicit choice. It marks the
printer as user-stopped exactly like the Stop button in the UI, so the job is
recorded as cancelled rather than failed.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime

import httpx
from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.notification import NotificationProvider

logger = logging.getLogger(__name__)

PREFIX = "obico"
POLL_TIMEOUT = 25  # seconds Telegram holds a getUpdates call open
RECONCILE_SECONDS = 60
ERROR_BACKOFF = 15


def fingerprint(task_name: str | None) -> str:
    """Short, stable id of a job name; callback_data is capped at 64 bytes."""
    return hashlib.sha1((task_name or "current job").encode()).hexdigest()[:10]


def failure_buttons(printer_id: int, task_name: str | None, actions=("pause", "stop")) -> dict:
    labels = {"pause": "⏸ Pausieren", "stop": "⏹ Abbrechen"}
    fp = fingerprint(task_name)
    return {
        "inline_keyboard": [
            [{"text": labels[a], "callback_data": f"{PREFIX}:{a}:{printer_id}:{fp}"} for a in actions]
        ]
    }


def parse_callback(data: str | None) -> tuple[str, int, str] | None:
    try:
        prefix, action, printer_id, fp = (data or "").split(":")
        if prefix == PREFIX and action in ("pause", "stop"):
            return action, int(printer_id), fp
    except ValueError:
        pass
    return None


def _current_task(status) -> str:
    # Same key the Obico loop uses for "this print".
    return getattr(status, "task_name", None) or getattr(status, "subtask_name", "") or ""


async def _printer_name(printer_id: int) -> str:
    from backend.app.services.obico_actions import _get_printer_name

    return await _get_printer_name(printer_id)


async def apply_action(action: str, printer_id: int, fp: str) -> tuple[str, dict | None]:
    """Run a pressed button. Returns (result line, buttons to leave on the alert)."""
    from backend.app.services.printer_manager import printer_manager

    name = await _printer_name(printer_id)
    client = printer_manager.get_client(printer_id)
    status = printer_manager.get_status(printer_id)
    if not client or not status or not printer_manager.is_connected(printer_id):
        return f"⚠️ {name} ist nicht verbunden – nichts geändert.", None
    task = _current_task(status)
    if fingerprint(task) != fp:
        return f"ℹ️ {name} druckt inzwischen etwas anderes – nichts geändert.", None

    state = str(getattr(status, "state", "") or "").upper()
    stamp = datetime.now().strftime("%H:%M")
    if action == "pause":
        if state == "PAUSE":
            return f"ℹ️ {name} ist schon pausiert.", failure_buttons(printer_id, task, ("stop",))
        if state != "RUNNING":
            return f"ℹ️ {name} druckt nicht mehr – nichts geändert.", None
        if not client.pause_print():
            return f"⚠️ Pausieren von {name} fehlgeschlagen.", failure_buttons(printer_id, task)
        logger.warning("Telegram: printer %s paused from a failure alert", printer_id)
        return f"⏸ {name} pausiert ({stamp}).", failure_buttons(printer_id, task, ("stop",))

    if state not in ("RUNNING", "PAUSE"):
        return f"ℹ️ {name} druckt nicht mehr – nichts geändert.", None
    if not client.stop_print():
        return f"⚠️ Abbrechen von {name} fehlgeschlagen.", failure_buttons(printer_id, task, ("stop",))
    try:
        from backend.app.main import mark_printer_stopped_by_user

        mark_printer_stopped_by_user(printer_id)
    except Exception as e:
        logger.warning("Telegram: failed to mark printer %s as user-stopped: %s", printer_id, e)
    logger.warning("Telegram: printer %s stopped from a failure alert", printer_id)
    return f"⏹ {name} abgebrochen ({stamp}).", None


class TelegramActionService:
    """One long-poll task per bot token of a Telegram provider with AI alerts."""

    def __init__(self):
        self._task: asyncio.Task | None = None
        self._pollers: dict[str, asyncio.Task] = {}
        self._chats: dict[str, set[str]] = {}  # bot token -> chats allowed to press buttons

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._reconcile_loop())

    def stop(self):
        for task in [self._task, *self._pollers.values()]:
            if task:
                task.cancel()
        self._task, self._pollers = None, {}

    async def _providers(self) -> dict[str, set[str]]:
        """bot token -> chat ids allowed to press buttons."""
        async with async_session() as db:
            result = await db.execute(
                select(NotificationProvider).where(
                    NotificationProvider.enabled.is_(True),
                    NotificationProvider.provider_type == "telegram",
                    NotificationProvider.on_ai_failure_detection.is_(True),
                )
            )
            tokens: dict[str, set[str]] = {}
            for provider in result.scalars().all():
                config = json.loads(provider.config) if isinstance(provider.config, str) else provider.config
                token, chat = (config.get("bot_token") or "").strip(), str(config.get("chat_id") or "").strip()
                if token and chat:
                    tokens.setdefault(token, set()).add(chat)
            return tokens

    async def _reconcile_loop(self):
        while True:
            try:
                wanted = await self._providers()
                for token in list(self._pollers):
                    if token not in wanted:
                        self._pollers.pop(token).cancel()
                self._chats = wanted
                for token in wanted:
                    poller = self._pollers.get(token)
                    if poller is None or poller.done():
                        self._pollers[token] = asyncio.create_task(self._poll(token))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Telegram actions: provider refresh failed: %s", e)
            await asyncio.sleep(RECONCILE_SECONDS)

    async def _poll(self, token: str):
        api = f"https://api.telegram.org/bot{token}"
        offset = None
        async with httpx.AsyncClient(timeout=POLL_TIMEOUT + 10) as client:
            # Presses made while Bambuddy was down are dropped, not replayed hours later.
            try:
                pending = (await client.get(f"{api}/getUpdates", params={"offset": -1, "timeout": 0})).json()
                if pending.get("result"):
                    offset = pending["result"][-1]["update_id"] + 1
            except Exception as e:
                logger.warning("Telegram actions: could not skip old updates: %s", e)
            while True:
                try:
                    params = {"timeout": POLL_TIMEOUT, "allowed_updates": json.dumps(["callback_query"])}
                    if offset is not None:
                        params["offset"] = offset
                    body = (await client.get(f"{api}/getUpdates", params=params)).json()
                    if not body.get("ok"):
                        raise RuntimeError(body.get("description") or "getUpdates failed")
                    for update in body.get("result", []):
                        offset = update["update_id"] + 1
                        if "callback_query" in update:
                            await self.handle(client, api, update["callback_query"], self._chats.get(token, set()))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("Telegram actions: polling failed: %s", e)
                    await asyncio.sleep(ERROR_BACKOFF)

    async def handle(self, client: httpx.AsyncClient, api: str, query: dict, chats: set[str]):
        message = query.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        parsed = parse_callback(query.get("data"))
        if parsed is None:
            await client.post(f"{api}/answerCallbackQuery", json={"callback_query_id": query["id"]})
            return
        if chat_id not in chats:
            logger.warning("Telegram actions: ignored a button press from chat %s", chat_id)
            await client.post(
                f"{api}/answerCallbackQuery", json={"callback_query_id": query["id"], "text": "Nicht erlaubt."}
            )
            return

        line, buttons = await apply_action(*parsed)
        await client.post(f"{api}/answerCallbackQuery", json={"callback_query_id": query["id"], "text": line})
        # Write the outcome under the alert and leave only the buttons that still make sense.
        markup = buttons or {"inline_keyboard": []}
        edit = {"chat_id": message["chat"]["id"], "message_id": message["message_id"], "reply_markup": markup}
        if "caption" in message:
            edit.update(caption=f"{message['caption']}\n\n{line}", caption_entities=message.get("caption_entities", []))
            await client.post(f"{api}/editMessageCaption", json=edit)
        elif "text" in message:
            edit.update(text=f"{message['text']}\n\n{line}", entities=message.get("entities", []))
            await client.post(f"{api}/editMessageText", json=edit)


telegram_action_service = TelegramActionService()
