"""The entrypoint, run against a temporary home with a stand-in `claude`.

The JOSUI_* environment prefix is inherited from crunchtools/josui, where this
image began, and is kept deliberately: the units that run it were written
against that contract. Renaming it is its own change.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "rootfs/usr/local/bin/developer-entrypoint"

FAKE_CLAUDE = """#!/bin/bash
printf '%s\\n' "$@" > "$HOME/claude-args"
echo "status screen"
echo "an error" >&2
env > "$HOME/claude-env"
pwd > "$HOME/claude-cwd"
"""


class EntrypointTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.skills = self.root / "josui-skills"
        bin_dir = self.root / "bin"
        for path in (
            self.home,
            self.config / "hooks",
            self.skills / "skills/alpha",
            self.skills / "skills/beta",
            self.skills / "templates",
            bin_dir,
        ):
            path.mkdir(parents=True)
        (self.config / "CLAUDE.md").write_text("# Josui\n")
        (self.config / "settings.json").write_text("{}\n")
        (self.config / "hooks/deny-local-memory.sh").write_text("#!/bin/bash\n")
        claude = bin_dir / "claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "JOSUI_CONFIG_DIR": str(self.config),
            "JOSUI_SKILLS_DIR": str(self.skills),
            "TRENTINA_MCP_URL": "http://192.0.2.2:8019/gateway/agent1/mcp",
            "TRENTINA_MCP_TOKEN": "s3cret-token",
            "TRENTINA_GIT_URL": "http://192.0.2.2:8019/git/",
        }

    def run_entrypoint(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(ENTRYPOINT), *args], env=env or self.env, capture_output=True, text=True, check=False
        )

    def test_assembles_the_home_and_serves_remote_control(self) -> None:
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        claude_dir = self.home / ".claude"
        self.assertEqual((claude_dir / "CLAUDE.md").read_text(), "# Josui\n")
        self.assertFalse((claude_dir / "settings.json").is_symlink())
        # Identity hooks are not copied: only the image's managed hooks run.
        self.assertFalse((claude_dir / "hooks/deny-local-memory.sh").exists())
        self.assertEqual(sorted(p.name for p in (claude_dir / "skills").iterdir()), ["alpha", "beta"])
        self.assertEqual((claude_dir / "skills/alpha").resolve(), (self.skills / "skills/alpha").resolve())
        self.assertEqual((claude_dir / "templates").resolve(), (self.skills / "templates").resolve())
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[0], "remote-control")
        self.assertIn("--remote-control-session-name-prefix", args)
        self.assertEqual(args[-1], "josui")
        self.assertEqual((self.home / "claude-cwd").read_text().strip(), str(self.home / "work"))

    def test_remote_control_status_output_is_dropped_and_errors_are_kept(self) -> None:
        result = self.run_entrypoint()
        self.assertNotIn("status screen", result.stdout)
        self.assertIn("an error", result.stderr)

    def test_gateway_is_merged_into_existing_state(self) -> None:
        state = self.home / ".claude.json"
        state.write_text(json.dumps({"hasCompletedOnboarding": True, "mcpServers": {"stale": {}}}))
        self.assertEqual(self.run_entrypoint().returncode, 0)
        data = json.loads(state.read_text())
        self.assertTrue(data["hasCompletedOnboarding"])
        self.assertEqual(list(data["mcpServers"]), ["trentina"])
        self.assertEqual(data["mcpServers"]["trentina"]["url"], self.env["TRENTINA_MCP_URL"])
        self.assertEqual(data["mcpServers"]["trentina"]["headers"]["Authorization"], "Bearer s3cret-token")
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)

    def test_token_is_not_handed_to_the_agent_process(self) -> None:
        self.run_entrypoint()
        self.assertNotIn("s3cret-token", (self.home / "claude-env").read_text())

    def git(self, *args: str) -> str:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        return subprocess.run(
            ["git", "config", *args], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_git_goes_to_the_gateway_route_with_the_gateway_token(self) -> None:
        self.run_entrypoint()
        self.run_entrypoint()  # a restart does not add a second include
        gitconf = self.home / ".config/josui/gateway.gitconfig"
        self.assertEqual(stat.S_IMODE(gitconf.stat().st_mode), 0o600)
        self.assertEqual(self.git("--global", "--get-all", "include.path"), str(gitconf))
        self.assertEqual(
            self.git("--get", "url.http://192.0.2.2:8019/git/github/.insteadOf"), "https://github.com/"
        )
        self.assertEqual(
            self.git("--get", "http.http://192.0.2.2:8019/git/.extraHeader"),
            "Authorization: Bearer s3cret-token",
        )

    def test_git_url_that_is_not_a_plain_url_is_ignored(self) -> None:
        env = {**self.env, "TRENTINA_GIT_URL": 'http://x"]\n[credential]\n\thelper = !touch /tmp/pwned'}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not a plain http(s) URL", result.stderr)
        self.assertFalse((self.home / ".config/josui/gateway.gitconfig").exists())

    def test_starts_without_a_gateway_and_says_so(self) -> None:
        env = {k: v for k, v in self.env.items() if not k.startswith("TRENTINA_")}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("starting without a gateway", result.stderr)
        self.assertFalse((self.home / ".claude.json").exists())

    def test_removed_skill_disappears_on_restart(self) -> None:
        self.run_entrypoint()
        shutil.rmtree(self.skills / "skills/beta")
        self.run_entrypoint()
        self.assertEqual([p.name for p in (self.home / ".claude/skills").iterdir()], ["alpha"])

    def test_arguments_run_instead_of_remote_control(self) -> None:
        result = self.run_entrypoint("claude", "auth", "login")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home / "claude-args").read_text().split(), ["auth", "login"])

    def session_env(self) -> dict[str, str]:
        agent = self.root / "bin/claude-agent"
        agent.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/agent-args"\nenv > "$HOME/agent-env"\nexit 7\n'
        )
        agent.chmod(0o755)
        claude = self.root / "bin/claude"
        claude.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/claude-args"\n'
            'env > "$HOME/claude-env"\nexec sleep 30\n'
        )
        return {
            **self.env,
            "JOSUI_MATRIX_HOMESERVER": "http://192.0.2.3:6167",
            "JOSUI_MATRIX_TOKEN_FILE": "/etc/josui-matrix/token",
            "JOSUI_MATRIX_ALLOW": "@alice:example.org",
            "JOSUI_PERMISSION_MODE": "bypassPermissions",
        }

    def agent_env(self) -> dict[str, str]:
        lines = (self.home / "agent-env").read_text().splitlines()
        return dict(line.split("=", 1) for line in lines if "=" in line)

    def test_text_session_runs_beside_remote_control_and_either_ending_stops_both(self) -> None:
        result = self.run_entrypoint(env=self.session_env())
        # The stand-in claude-agent exits 7 at once; the server is stopped too.
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn("a supervised process ended (status 7)", result.stderr)
        self.assertEqual((self.home / "claude-args").read_text().split()[0], "remote-control")
        self.assertEqual(
            (self.home / "agent-args").read_text().split(),
            ["run", "--", "--permission-mode", "bypassPermissions"],
        )

    def test_the_bridge_gets_the_matrix_settings_under_its_own_names(self) -> None:
        self.run_entrypoint(env=self.session_env())
        env = self.agent_env()
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_HOMESERVER"], "http://192.0.2.3:6167")
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_TOKEN_FILE"], "/etc/josui-matrix/token")
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_ALLOW"], "@alice:example.org")
        # The old channel's sync position, so texts sent across the upgrade arrive.
        self.assertEqual(env["CLAUDE_AGENT_MATRIX_STATE"], str(self.home / ".config/josui/matrix-sync"))
        self.assertNotIn("s3cret-token", (self.home / "agent-env").read_text())

    def test_josui_settings_reach_claude_agent(self) -> None:
        env = {
            **self.session_env(),
            "JOSUI_ANSWER_PERMISSIONS": "1",
            "JOSUI_MAX_TOOL_CALLS": "900",
            "JOSUI_MAX_REPEAT": "7",
        }
        self.run_entrypoint(env=env)
        agent = self.agent_env()
        self.assertEqual(
            (
                agent["CLAUDE_AGENT_ANSWER_PERMISSIONS"],
                agent["CLAUDE_AGENT_MAX_TOOL_CALLS"],
                agent["CLAUDE_AGENT_MAX_REPEAT"],
            ),
            ("1", "900", "7"),
        )

    def test_text_session_skips_onboarding_but_not_trust(self) -> None:
        self.run_entrypoint(env=self.session_env())
        state = json.loads((self.home / ".claude.json").read_text())
        self.assertTrue(state["hasCompletedOnboarding"])
        # Trusting the working directory stays the operator's call.
        self.assertNotIn("projects", state)
        self.assertIn("trentina", state["mcpServers"])

    def claude_that_runs_for(self, seconds: int) -> None:
        claude = self.root / "bin/claude"
        claude.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "$HOME/claude-args"\nexec sleep {seconds}\n')
        claude.chmod(0o755)

    def test_debug_file_is_asked_for_and_emptied_when_there_is_an_audit_dir(self) -> None:
        audit = self.root / "audit"
        audit.mkdir()
        stale = audit / "remote-control.log"
        stale.write_text("from the last container\n")
        result = self.run_entrypoint(env={**self.env, "CLAUDE_AGENT_AUDIT_DIR": str(audit)})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[args.index("--debug-file") + 1], str(stale))
        # The session name prefix stays last, and the stale log is gone.
        self.assertEqual(args[-1], "josui")
        self.assertEqual(stale.read_text(), "")

    def test_no_debug_file_when_there_is_nowhere_to_put_it(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertNotIn("--debug-file", (self.home / "claude-args").read_text())

    def test_an_explicit_debug_file_is_used(self) -> None:
        explicit = self.root / "somewhere-else.log"
        result = self.run_entrypoint(env={**self.env, "JOSUI_DEBUG_FILE": str(explicit)})
        self.assertEqual(result.returncode, 0, result.stderr)
        args = (self.home / "claude-args").read_text().split()
        self.assertEqual(args[args.index("--debug-file") + 1], str(explicit))

    def test_a_debug_file_that_cannot_be_written_is_reported_and_dropped(self) -> None:
        env = {**self.env, "JOSUI_DEBUG_FILE": str(self.root / "no-such-directory/debug.log")}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("running without a debug file", result.stderr)
        self.assertNotIn("--debug-file", (self.home / "claude-args").read_text())

    # The person's own terminal (JOSUI_TMUX), with a tmux that records its
    # arguments and reports no existing session.
    def fake_tmux(self) -> None:
        tmux = self.root / "bin/tmux"
        tmux.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/tmux-args"\n'
            '[ "$1" = has-session ] && exit 1\nexit 0\n'
        )
        tmux.chmod(0o755)

    def test_tmux_session_is_started_when_asked(self) -> None:
        self.fake_tmux()
        result = self.run_entrypoint(env={**self.env, "JOSUI_TMUX": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.home / "tmux-args").read_text().splitlines()
        self.assertEqual(calls[0], "has-session -t dev")
        self.assertEqual(
            calls[1],
            f"new-session -d -s dev -c {self.home / 'work'} claude --permission-mode acceptEdits",
        )
        self.assertIn("set-option -t dev remain-on-exit on", calls)
        self.assertIn("tmux session 'dev' is up", result.stderr)

    def test_a_tmux_session_that_will_not_start_is_not_reported_as_up(self) -> None:
        tmux = self.root / "bin/tmux"
        tmux.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/tmux-args"\n'
            '[ "$1" = new-session ] && exit 1\n[ "$1" = has-session ] && exit 1\nexit 0\n'
        )
        tmux.chmod(0o755)
        result = self.run_entrypoint(env={**self.env, "JOSUI_TMUX": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not start the tmux session", result.stderr)
        self.assertNotIn("is up", result.stderr)
        # It stopped there rather than carrying on to set-option.
        self.assertNotIn("set-option", (self.home / "tmux-args").read_text())

    def test_an_existing_tmux_session_is_left_alone(self) -> None:
        tmux = self.root / "bin/tmux"
        tmux.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$HOME/tmux-args"\nexit 0\n')
        tmux.chmod(0o755)
        self.run_entrypoint(env={**self.env, "JOSUI_TMUX": "1"})
        self.assertEqual((self.home / "tmux-args").read_text().splitlines(), ["has-session -t dev"])

    def test_a_seat_gets_a_git_identity_when_one_is_given(self) -> None:
        env = {**self.env, "JOSUI_GIT_NAME": "Josui", "JOSUI_GIT_EMAIL": "josui@crunchtools.com"}
        self.assertEqual(self.run_entrypoint(env=env).returncode, 0)
        self.assertEqual(self.git("--get", "user.name"), "Josui")
        self.assertEqual(self.git("--get", "user.email"), "josui@crunchtools.com")
        self.assertEqual(self.git("--get", "advice.detachedHead"), "false")

    def test_an_identity_already_set_is_not_overwritten(self) -> None:
        subprocess.run(
            ["git", "config", "--global", "user.email", "someone@else"],
            env={"HOME": str(self.home), "PATH": os.environ["PATH"]},
            check=True,
        )
        env = {**self.env, "JOSUI_GIT_EMAIL": "josui@crunchtools.com"}
        self.assertEqual(self.run_entrypoint(env=env).returncode, 0)
        self.assertEqual(self.git("--get", "user.email"), "someone@else")

    def test_no_git_identity_unless_one_is_given(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        with self.assertRaises(subprocess.CalledProcessError):
            self.git("--get", "user.name")

    def test_mounted_env_files_are_installed_private(self) -> None:
        src = self.config / "mcp-env"
        src.mkdir()
        key = src / "other.env"
        key.write_text("SOME_API_KEY=sk-test\n")
        key.chmod(0o644)
        (src / "notes.txt").write_text("not an env file\n")
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.home / ".config/mcp-env"
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)
        installed = dest / "other.env"
        self.assertEqual(stat.S_IMODE(installed.stat().st_mode), 0o600)
        self.assertEqual(installed.read_text(), "SOME_API_KEY=sk-test\n")
        # Only *.env is taken, so a README beside the keys is not copied in.
        self.assertFalse((dest / "notes.txt").exists())

    def test_gatehouse_env_points_at_the_gateway(self) -> None:
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        dest = self.home / ".config/mcp-env"
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o700)
        written = dest / "gatehouse.env"
        self.assertEqual(stat.S_IMODE(written.stat().st_mode), 0o600)
        body = written.read_text()
        # The origin comes from TRENTINA_MCP_URL and only the path differs, so a
        # gateway that moves does not need this file edited too.
        self.assertIn(
            "GATEHOUSE_LLM_BASE_URL=http://192.0.2.2:8019/proxy/llm/openrouter-gatehouse/v1\n", body
        )
        # The seat's own gateway token is the key: no OpenRouter credential
        # exists in a seat any more (RT#1559).
        self.assertIn("OPENROUTER_API_KEY=s3cret-token\n", body)

    def test_a_mounted_gatehouse_env_wins(self) -> None:
        """Deleting the mounted key is the cutover, so a present one wins.

        A seat pushes many repos and each pins its own python-sdlc rev, so the
        Gatehouse a gate runs is the pushed repo's choice. Flipping this file
        while any such repo is below v0.5.2 breaks that repo's gate, so the
        image must not flip it on its own.
        """
        src = self.config / "mcp-env"
        src.mkdir()
        (src / "gatehouse.env").write_text("OPENROUTER_API_KEY=sk-raw-key-from-the-mount\n")
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.home / ".config/mcp-env/gatehouse.env").read_text(),
            "OPENROUTER_API_KEY=sk-raw-key-from-the-mount\n",
        )
        self.assertIn("NOT using the gateway route", result.stderr)

    def test_a_generated_gatehouse_env_is_refreshed_not_mistaken_for_a_mount(self) -> None:
        """$HOME persists, so a file this wrote last start is not a mount.

        Testing the destination would read its own output as an operator's
        choice and leave a rotated token stale.
        """
        self.assertEqual(self.run_entrypoint().returncode, 0)
        env = {**self.env, "TRENTINA_MCP_TOKEN": "rotated-token"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.home / ".config/mcp-env/gatehouse.env").read_text()
        self.assertIn("OPENROUTER_API_KEY=rotated-token\n", body)
        self.assertNotIn("s3cret-token", body)
        self.assertNotIn("NOT using the gateway route", result.stderr)

    def test_a_planted_symlink_does_not_capture_the_token(self) -> None:
        """$HOME is agent-writable, so the destination may be a trap."""
        dest = self.home / ".config/mcp-env"
        dest.mkdir(parents=True)
        target = self.home / "stolen.env"
        target.write_text("untouched\n")
        (dest / "gatehouse.env").symlink_to(target)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_text(), "untouched\n")
        written = dest / "gatehouse.env"
        self.assertFalse(written.is_symlink())
        self.assertIn("OPENROUTER_API_KEY=s3cret-token\n", written.read_text())
        self.assertEqual(stat.S_IMODE(written.stat().st_mode), 0o600)

    def test_a_symlinked_config_dir_is_refused(self) -> None:
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        (self.home / ".config").mkdir()
        (self.home / ".config/mcp-env").symlink_to(elsewhere)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertIn("refusing to write a token through it", result.stderr)

    def test_a_symlinked_ancestor_is_refused(self) -> None:
        """Checking only the last component leaves ~/.config as the way in."""
        elsewhere = self.home / "elsewhere"
        elsewhere.mkdir()
        (self.home / ".config").symlink_to(elsewhere)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((elsewhere / "mcp-env").exists())
        self.assertIn(".config is a symlink", result.stderr)

    def test_mktemp_failing_writes_no_token(self) -> None:
        fake = pathlib.Path(self.env["PATH"].split(os.pathsep)[0]) / "mktemp"
        fake.write_text(
            "#!/bin/bash\n"
            # Only the gatehouse.env template: the state file above needs a
            # working mktemp to get that far.
            'case "$1" in *gatehouse.env*) exit 1 ;; esac\n'
            'exec /usr/bin/mktemp "$@"\n'
        )
        fake.chmod(0o755)
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / ".config/mcp-env/gatehouse.env").exists())
        self.assertIn("cannot create a temporary file", result.stderr)

    def test_no_temporary_file_is_left_behind(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        names = sorted(p.name for p in (self.home / ".config/mcp-env").iterdir())
        self.assertEqual(names, ["gatehouse.env"])

    def test_no_gateway_token_leaves_the_mounted_gatehouse_env_alone(self) -> None:
        src = self.config / "mcp-env"
        src.mkdir()
        (src / "gatehouse.env").write_text("OPENROUTER_API_KEY=sk-raw\n")
        env = {k: v for k, v in self.env.items() if k != "TRENTINA_MCP_TOKEN"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Failing here would take away the only Gatehouse such a seat can run.
        self.assertEqual(
            (self.home / ".config/mcp-env/gatehouse.env").read_text(),
            "OPENROUTER_API_KEY=sk-raw\n",
        )
        self.assertIn("left as mounted", result.stderr)

    def test_the_url_is_not_settable_on_its_own(self) -> None:
        """Anything able to set it could redirect the gateway token."""
        env = {**self.env, "TRENTINA_LLM_URL": "http://192.0.2.9:9000/collect"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.home / ".config/mcp-env/gatehouse.env").read_text()
        self.assertNotIn("192.0.2.9", body)
        self.assertIn(
            "GATEHOUSE_LLM_BASE_URL=http://192.0.2.2:8019/proxy/llm/openrouter-gatehouse/v1\n", body
        )

    def test_a_token_with_no_gateway_url_leaves_the_mount_alone(self) -> None:
        src = self.config / "mcp-env"
        src.mkdir()
        (src / "gatehouse.env").write_text("OPENROUTER_API_KEY=sk-raw\n")
        env = {k: v for k, v in self.env.items() if k != "TRENTINA_MCP_URL"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.home / ".config/mcp-env/gatehouse.env").read_text(),
            "OPENROUTER_API_KEY=sk-raw\n",
        )
        self.assertIn("left as mounted", result.stderr)

    def test_no_env_dir_still_writes_the_gateway_gatehouse_env(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        dest = self.home / ".config/mcp-env"
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["gatehouse.env"])

    def test_an_empty_env_dir_is_reported_not_fatal(self) -> None:
        (self.config / "mcp-env").mkdir()
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("has no *.env files", result.stderr)

    def test_no_tmux_session_unless_it_is_asked_for(self) -> None:
        self.fake_tmux()
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.home / "tmux-args").exists())

    def test_tmux_missing_from_the_image_is_reported_not_fatal(self) -> None:
        env = {**self.env, "JOSUI_TMUX": "1", "JOSUI_TMUX_CMD": "tmux-that-is-not-installed"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no tmux-that-is-not-installed in this image", result.stderr)

    def test_without_a_homeserver_there_is_no_text_session(self) -> None:
        agent = self.root / "bin/claude-agent"
        agent.write_text('#!/bin/bash\ntouch "$HOME/agent-ran"\n')
        agent.chmod(0o755)
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertFalse((self.home / "agent-ran").exists())


if __name__ == "__main__":
    unittest.main()
