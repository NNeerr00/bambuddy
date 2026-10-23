"""Telegram pause/stop buttons on AI failure alerts (Mechonix)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services import telegram_actions
from backend.app.services.telegram_actions import (
    apply_action,
    failure_buttons,
    fingerprint,
    parse_callback,
)

JOB = "netgear-gs308ep-tp-link-tl-sg108pe-10zoll-rackmount-2x__100003"


def manager(state="RUNNING", task=JOB, connected=True, pause_ok=True, stop_ok=True):
    client = MagicMock()
    client.pause_print.return_value = pause_ok
    client.stop_print.return_value = stop_ok
    status = SimpleNamespace(state=state, task_name=None, subtask_name=task)
    return SimpleNamespace(
        get_client=lambda pid: client,
        get_status=lambda pid: status,
        is_connected=lambda pid: connected,
        client=client,
    )


@pytest.fixture(autouse=True)
def _printer_name():
    with patch.object(telegram_actions, "_printer_name", new=AsyncMock(return_value="6-3")):
        yield


class TestButtons:
    def test_callback_roundtrip_fits_telegram_limit(self):
        markup = failure_buttons(36, JOB)
        row = markup["inline_keyboard"][0]
        assert [b["text"] for b in row] == ["⏸ Pausieren", "⏹ Abbrechen"]
        for button in row:
            assert len(button["callback_data"].encode()) <= 64
        assert parse_callback(row[1]["callback_data"]) == ("stop", 36, fingerprint(JOB))

    def test_rejects_foreign_callback_data(self):
        for data in (None, "", "obico:delete:36:abc", "other:stop:36:abc", "obico:stop:x:abc", "a:b"):
            assert parse_callback(data) is None


class TestApplyAction:
    async def test_pause_pauses_and_leaves_only_stop(self):
        mgr = manager()
        (line, buttons), _ = await self._run("pause", mgr)
        mgr.client.pause_print.assert_called_once()
        mgr.client.stop_print.assert_not_called()
        assert line.startswith("⏸ 6-3 pausiert")
        assert [b["text"] for b in buttons["inline_keyboard"][0]] == ["⏹ Abbrechen"]

    async def test_stop_stops_without_question_and_counts_as_cancelled(self):
        mgr = manager()
        (line, buttons), mark = await self._run("stop", mgr)
        mgr.client.stop_print.assert_called_once()
        mark.assert_called_once_with(36)
        assert line.startswith("⏹ 6-3 abgebrochen")
        assert buttons is None

    async def test_stop_also_works_when_paused(self):
        mgr = manager(state="PAUSE")
        (line, _), _ = await self._run("stop", mgr)
        mgr.client.stop_print.assert_called_once()

    async def test_old_alert_never_touches_the_next_print(self):
        mgr = manager(task="ubiquiti-cloud-gateway__100015")
        (line, buttons), mark = await self._run("stop", mgr)
        mgr.client.stop_print.assert_not_called()
        mark.assert_not_called()
        assert "etwas anderes" in line and buttons is None

    @pytest.mark.parametrize("state", ["FINISH", "IDLE", "FAILED"])
    async def test_nothing_to_do_when_not_printing(self, state):
        mgr = manager(state=state)
        (line, _), _ = await self._run("stop", mgr)
        mgr.client.stop_print.assert_not_called()
        assert "druckt nicht mehr" in line

    async def test_disconnected_printer(self):
        mgr = manager(connected=False)
        (line, _), _ = await self._run("pause", mgr)
        mgr.client.pause_print.assert_not_called()
        assert "nicht verbunden" in line

    async def test_failed_command_keeps_the_button(self):
        mgr = manager(stop_ok=False)
        (line, buttons), mark = await self._run("stop", mgr)
        mark.assert_not_called()
        assert "fehlgeschlagen" in line and buttons is not None

    @staticmethod
    async def _run(action, mgr):
        with (
            patch("backend.app.services.printer_manager.printer_manager", mgr),
            patch("backend.app.main.mark_printer_stopped_by_user") as mark,
        ):
            return await apply_action(action, 36, fingerprint(JOB)), mark


class TestHandle:
    def query(self, chat_id=111, data=None):
        return {
            "id": "q1",
            "data": data or failure_buttons(36, JOB)["inline_keyboard"][0][1]["callback_data"],
            "message": {
                "message_id": 9,
                "chat": {"id": chat_id},
                "caption": "Possible Print Failure Detected\n6-3: job",
                "caption_entities": [{"type": "bold", "offset": 0, "length": 31}],
            },
        }

    async def test_own_chat_runs_action_and_writes_the_outcome_under_the_alert(self):
        client = MagicMock(post=AsyncMock())
        with patch.object(telegram_actions, "apply_action", new=AsyncMock(return_value=("⏹ 6-3 abgebrochen (23:41).", None))) as act:
            await telegram_actions.TelegramActionService().handle(client, "API", self.query(), {"111"})
        act.assert_awaited_once_with("stop", 36, fingerprint(JOB))
        calls = {c.args[0]: c.kwargs["json"] for c in client.post.await_args_list}
        assert calls["API/answerCallbackQuery"]["text"] == "⏹ 6-3 abgebrochen (23:41)."
        edit = calls["API/editMessageCaption"]
        assert edit["caption"].endswith("\n\n⏹ 6-3 abgebrochen (23:41).")
        assert edit["caption_entities"][0]["type"] == "bold"
        assert edit["reply_markup"] == {"inline_keyboard": []}

    async def test_foreign_chat_is_refused(self):
        client = MagicMock(post=AsyncMock())
        with patch.object(telegram_actions, "apply_action", new=AsyncMock()) as act:
            await telegram_actions.TelegramActionService().handle(client, "API", self.query(chat_id=999), {"111"})
        act.assert_not_awaited()
        assert client.post.await_args.kwargs["json"]["text"] == "Nicht erlaubt."


class TestAlertCarriesButtons:
    async def test_ai_alert_to_telegram_has_buttons_other_events_do_not(self):
        from backend.app.services.notification_service import NotificationService

        service = NotificationService()
        provider = MagicMock(provider_type="telegram", quiet_hours_enabled=False)
        provider.config = json.dumps({"bot_token": "T", "chat_id": "111"})
        with patch.object(service, "_send_telegram", new=AsyncMock(return_value=(True, "ok"))) as send:
            await service._send_to_provider(
                provider, "Possible Print Failure Detected", "6-3", event_type="ai_failure_detection",
                variables={"printer_id": 36, "job_key": JOB},
            )
            await service._send_to_provider(provider, "Print Completed", "6-3", event_type="print_complete",
                                            variables={"printer": "6-3"})
        first, second = send.await_args_list
        assert parse_callback(first.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]) == (
            "pause", 36, fingerprint(JOB))
        assert second.kwargs["reply_markup"] is None

    async def test_variables_carry_printer_and_exact_job(self):
        from backend.app.services.notification_service import NotificationService

        service = NotificationService()
        with (
            patch.object(service, "_get_providers_for_event", new=AsyncMock(return_value=[MagicMock()])),
            patch.object(service, "_build_message_from_template", new=AsyncMock(return_value=("T", "M"))),
            patch.object(service, "_send_to_providers", new=AsyncMock()) as send,
        ):
            await service.on_ai_failure_detection(
                printer_id=36, printer_name="6-3", task_name=JOB, confidence=2.9, action="notify", db=AsyncMock()
            )
        variables = send.await_args.kwargs["variables"]
        assert (variables["printer_id"], variables["job_key"]) == (36, JOB)
