"""seat-state, the managed hook that tells /agents whether a seat is busy and who drives it.

The events are shaped as Claude Code 2.1.304 sends them, captured from the real
binary: a typed prompt reaches UserPromptSubmit as `prompt`, a cross-session
message reaches it too (wrapped in <cross-session-message>), a turn ends with
Stop (or StopFailure on an API error), and /clear starts the new conversation
with SessionStart source=clear.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOTFS = Path(__file__).resolve().parent.parent / "rootfs"
SEAT_STATE = ROOTFS / "usr/libexec/claude-seat/seat-state"
MANAGED = ROOTFS / "etc/claude-code/managed-settings.d/40-claude-seat.json"

FIRST = "4c3d8432-626e-4ab4-9b5d-6e5e958ce0be"
CLEARED = "7bbc1c5f-636e-473c-9820-c34794004e1c"
# As a real SendMessage reaches UserPromptSubmit: the wrapper, nothing before it.
PEER = (
    '<cross-session-message from="uds:/tmp/cc-socks/121.sock" from-name="Josui Prime" from-mode="bypass">\n'
    "[a99] do the thing\n</cross-session-message>"
)
# As a background task's result reaches UserPromptSubmit.
TASK = (
    "<task-notification>\n<task-id>bq2xk9</task-id>\n<status>completed</status>\n"
    "<summary>Background command finished</summary>\n</task-notification>"
)


class SeatStateTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.state = self.home / ".claude/seat-state.json"
        self.pin = self.home / ".config/josui/session-id"

    def hook(self, event: dict[str, str]) -> subprocess.CompletedProcess[str]:
        # Stop, StopFailure and Notification all run `seat-state stop`.
        name = {"UserPromptSubmit": "prompt", "SessionStart": "start"}.get(event["hook_event_name"], "stop")
        result = subprocess.run(
            [str(SEAT_STATE), name],
            input=json.dumps(event),
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        # A hook must never block the prompt, and UserPromptSubmit's stdout
        # would be added to the conversation.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def prompt(self, text: str, session: str = FIRST) -> subprocess.CompletedProcess[str]:
        return self.hook({"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": text})

    def start(self, source: str, session: str = CLEARED) -> subprocess.CompletedProcess[str]:
        return self.hook({"hook_event_name": "SessionStart", "session_id": session, "source": source})

    def stop(self, event: str = "Stop", session: str = FIRST) -> subprocess.CompletedProcess[str]:
        return self.hook({"hook_event_name": event, "session_id": session})

    def seat(self) -> dict[str, str]:
        return json.loads(self.state.read_text())

    def test_a_typed_prompt_marks_the_seat_busy_and_manual(self) -> None:
        self.prompt("fix the flaky test in trentina")
        seat = self.seat()
        self.assertEqual(seat["state"], "busy")
        self.assertEqual(seat["owner"], "manual")
        self.assertEqual(seat["task"], "fix the flaky test in trentina")
        self.assertEqual(seat["session_id"], FIRST)
        self.assertRegex(seat["since"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")

    def test_the_task_is_cut_to_120_characters(self) -> None:
        self.prompt("x" * 300)
        self.assertEqual(self.seat()["task"], "x" * 120)

    def test_the_first_prompt_of_the_session_stays_the_task(self) -> None:
        self.prompt("first thing")
        since = self.seat()["since"]
        self.prompt("and another thing")
        self.assertEqual(self.seat()["task"], "first thing")
        self.assertEqual(self.seat()["since"], since)

    def test_a_new_session_takes_a_new_task(self) -> None:
        self.prompt("first thing")
        self.prompt("in a new conversation", session=CLEARED)
        self.assertEqual(self.seat()["task"], "in a new conversation")
        self.assertEqual(self.seat()["session_id"], CLEARED)

    def test_a_corrupt_state_file_is_reported_and_replaced(self) -> None:
        for broken in ("not json{", "[1, 2]"):
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text(broken)
            result = self.prompt("first thing")
            self.assertIn("seat-state", result.stderr)
            self.assertEqual(self.seat()["task"], "first thing")

    def test_a_cross_session_message_is_not_a_person(self) -> None:
        self.prompt(PEER)
        seat = self.seat()
        self.assertNotIn("state", seat)
        self.assertNotIn("owner", seat)
        self.assertNotIn("task", seat)

    def test_a_typed_prompt_quoting_the_marker_is_still_a_person(self) -> None:
        self.prompt("why does the hook skip <cross-session-message from=x>? check it")
        self.assertEqual(self.seat()["owner"], "manual")
        self.assertEqual(self.seat()["state"], "busy")

    def test_a_typed_prompt_quoting_a_whole_peer_message_is_still_a_person(self) -> None:
        self.prompt("Josui sent this, what does it mean?\n" + PEER)
        self.assertEqual(self.seat()["owner"], "manual")

    def test_a_cross_session_message_leaves_a_manual_task_alone(self) -> None:
        self.prompt("first thing")
        before = self.seat()
        self.prompt(PEER)
        after = self.seat()
        for key in ("state", "owner", "task", "since", "session_id"):
            self.assertEqual(after[key], before[key])

    def test_a_typed_prompt_starts_a_turn(self) -> None:
        self.prompt("first thing")
        seat = self.seat()
        self.assertEqual(seat["turn"], "active")
        self.assertRegex(seat["turn_started"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")

    # Josui Prime's assignments reach an idle seat as a peer message, not a
    # typed prompt; the seat is just as mid-turn.
    def test_a_cross_session_message_starts_a_turn(self) -> None:
        self.prompt("first thing")
        self.stop()
        self.prompt(PEER)
        seat = self.seat()
        self.assertEqual(seat["turn"], "active")
        self.assertNotIn("turn_ended", seat)
        self.assertEqual(seat["owner"], "manual")

    def test_stop_ends_the_turn(self) -> None:
        self.prompt("first thing")
        self.stop()
        seat = self.seat()
        self.assertEqual(seat["turn"], "idle")
        self.assertRegex(seat["turn_ended"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
        self.assertEqual(seat["task"], "first thing")
        self.assertEqual(seat["state"], "busy")

    def test_a_task_notification_is_not_a_person(self) -> None:
        self.prompt(TASK)
        seat = self.seat()
        self.assertEqual(seat["turn"], "active")
        self.assertNotIn("owner", seat)
        self.assertNotIn("task", seat)

    def test_a_task_notification_leaves_a_manual_task_alone(self) -> None:
        self.prompt("first thing")
        self.stop()
        before = self.seat()
        self.prompt(TASK, session=CLEARED)
        after = self.seat()
        self.assertEqual(after["turn"], "active")
        for key in ("state", "owner", "task", "since", "session_id"):
            self.assertEqual(after[key], before[key])

    def idle_prompt(self) -> subprocess.CompletedProcess[str]:
        return self.hook(
            {"hook_event_name": "Notification", "session_id": FIRST, "notification_type": "idle_prompt"}
        )

    def backdate_turn(self, seconds: int) -> None:
        seat = self.seat()
        seat["turn_started"] = (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat(timespec="seconds")
        self.state.write_text(json.dumps(seat))

    # Claude Code runs no Stop after Esc; idle_prompt follows a minute later.
    def test_idle_prompt_ends_an_interrupted_turn(self) -> None:
        self.prompt("first thing")
        self.backdate_turn(90)
        self.idle_prompt()
        self.assertEqual(self.seat()["turn"], "idle")

    # A prompt that lands while idle_prompt's hook is on its way started a
    # turn the notice is not about.
    def test_idle_prompt_leaves_a_turn_that_just_started(self) -> None:
        self.prompt("first thing")
        self.backdate_turn(90)
        self.prompt(PEER)
        self.idle_prompt()
        self.assertEqual(self.seat()["turn"], "active")

    # With no usable turn_started there is no way to tell, and a seat stuck
    # active would block the clear for good.
    def test_idle_prompt_ends_a_turn_with_no_usable_start(self) -> None:
        naive = datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
        for started in (None, "not a time", naive):
            self.prompt("first thing")
            seat = self.seat()
            if started is None:
                del seat["turn_started"]
            else:
                seat["turn_started"] = started
            self.state.write_text(json.dumps(seat))
            self.idle_prompt()
            self.assertEqual(self.seat()["turn"], "idle")

    def test_a_hook_waits_for_the_lock(self) -> None:
        self.state.parent.mkdir(parents=True)
        event = self.home / "event.json"
        event.write_text(json.dumps({"session_id": FIRST, "prompt": "first thing"}))
        with open(self.state.parent / ".seat-state.lock", "a") as lock, event.open() as stdin:
            fcntl.flock(lock, fcntl.LOCK_EX)
            waiting = subprocess.Popen(
                [str(SEAT_STATE), "prompt"],
                stdin=stdin,
                env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
            )
            time.sleep(0.5)
            self.assertIsNone(waiting.poll())
            self.assertFalse(self.state.exists())
        self.assertEqual(waiting.wait(timeout=10), 0)
        self.assertEqual(self.seat()["task"], "first thing")

    def test_idle_prompt_keeps_the_turn_end_stop_recorded(self) -> None:
        self.prompt("first thing")
        self.stop()
        before = self.state.read_text()
        self.idle_prompt()
        self.assertEqual(self.state.read_text(), before)

    def test_a_failed_turn_is_over_too(self) -> None:
        self.prompt(PEER)
        self.stop("StopFailure")
        self.assertEqual(self.seat()["turn"], "idle")

    def test_stop_on_a_seat_with_no_state_records_the_turn(self) -> None:
        self.stop()
        self.assertEqual(self.seat()["turn"], "idle")

    def test_clear_frees_the_seat(self) -> None:
        self.prompt("first thing")
        self.start("clear")
        seat = self.seat()
        self.assertEqual(seat["state"], "free")
        self.assertEqual(seat["session_id"], CLEARED)
        self.assertNotIn("task", seat)
        self.assertEqual(seat["turn"], "idle")

    # claude-worker resumes the pinned UUID: re-pinned to the cleared
    # conversation, a respawn does not bring the old one back.
    def test_clear_repins_the_session(self) -> None:
        self.pin.parent.mkdir(parents=True)
        self.pin.write_text(FIRST + "\n")
        self.start("clear")
        self.assertEqual(self.pin.read_text(), CLEARED + "\n")
        self.assertEqual(list(self.pin.parent.iterdir()), [self.pin])

    def test_clear_pins_even_with_no_pin_yet(self) -> None:
        self.start("clear")
        self.assertEqual(self.pin.read_text(), CLEARED + "\n")

    # A seat that died mid-turn comes back with no turn running.
    def test_startup_and_resume_end_the_turn_and_keep_the_seat(self) -> None:
        for source in ("startup", "resume"):
            self.prompt("first thing")
            before = self.seat()
            self.start(source)
            seat = self.seat()
            self.assertEqual(seat["turn"], "idle")
            for key in ("state", "owner", "task", "since", "session_id"):
                self.assertEqual(seat[key], before[key])
        self.assertFalse(self.pin.exists())

    # Auto-compaction can fire mid-turn.
    def test_compact_changes_nothing(self) -> None:
        self.prompt("first thing")
        before = self.state.read_text()
        self.start("compact")
        self.assertEqual(self.state.read_text(), before)
        self.assertFalse(self.pin.exists())

    def test_a_session_id_that_is_not_a_uuid_is_not_pinned(self) -> None:
        self.start("clear", session="../../etc/passwd")
        self.assertFalse(self.pin.exists())

    def test_bad_input_is_reported_not_fatal(self) -> None:
        result = subprocess.run(
            [str(SEAT_STATE), "prompt"],
            input="not json",
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("seat-state", result.stderr)

    def test_an_unwritable_home_is_reported_not_fatal(self) -> None:
        (self.home / ".claude").write_text("not a directory\n")
        result = self.prompt("first thing")
        self.assertIn("seat-state", result.stderr)


class ManagedSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = json.loads(MANAGED.read_text())

    def test_autocompact_at_500k(self) -> None:
        self.assertEqual(self.settings["autoCompactWindow"], 500000)

    def test_hooks_run_seat_state_from_the_image(self) -> None:
        hooks = self.settings["hooks"]
        commands = {
            event: [h["command"] for group in hooks[event] for h in group["hooks"]]
            for event in ("UserPromptSubmit", "Stop", "StopFailure", "Notification", "SessionStart")
        }
        state, desk = "/usr/libexec/claude-seat/seat-state", "/usr/libexec/claude-seat/ashigaru-hook"
        # Both until the Desk cutover: seat-state.json and the Desk.
        self.assertEqual(commands["UserPromptSubmit"], [f"{state} prompt", f"{desk} prompt-submit"])
        self.assertEqual(commands["Stop"], [f"{state} stop", f"{desk} stop"])
        self.assertEqual(commands["StopFailure"], [f"{state} stop", f"{desk} stop"])
        self.assertEqual(commands["Notification"], [f"{state} stop", f"{desk} stop"])
        self.assertEqual([group.get("matcher") for group in hooks["Notification"]], ["idle_prompt"])
        self.assertEqual(commands["SessionStart"], [f"{state} start", f"{desk} session-start"])
        self.assertTrue(SEAT_STATE.stat().st_mode & 0o111)
