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
import time
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "rootfs/usr/local/bin/claude-entrypoint"
WORKER_CLAUDE = ENTRYPOINT.with_name("claude-worker")

FAKE_CLAUDE = """#!/bin/bash
printf '%s\\n' "$@" > "$HOME/claude-args"
echo "status screen"
echo "an error" >&2
env > "$HOME/claude-env"
pwd > "$HOME/claude-cwd"
"""


# Shell for a stand-in Claude: SIGKILL the sleep of every other child of the
# entrypoint (the sync loop), as something outside the seat might.
#
# The loop may not have started its sleep yet (a loaded machine, as under
# `sdlc pytest`), so look again until a sleep has been killed.
# Under sdlc the nested container sees its parent's /proc, whose pids are in
# another namespace: match on /proc's pids, then kill by our own.
KILL_LOOP_SLEEP = r"""killed=
read -r self _ _ parent _ < /proc/self/stat
for attempt in $(seq 100); do
  for f in /proc/[0-9]*/stat; do
    read -r pid comm _ ppid _ 2>/dev/null < "$f" && echo "$pid $comm $ppid"
  done > "$HOME/ps"
  loops=" "
  while read -r pid comm ppid; do
    [ "$ppid" = "$parent" ] && [ "$pid" != "$self" ] && loops+="$pid "
  done < "$HOME/ps"
  while read -r pid comm ppid; do
    case $loops in *" $ppid "*)
      [ "$comm" = "(sleep)" ] || continue
      # kill wants the pid in our namespace: the last NSpid field. Read it
      # with Name and PPid from one status, and kill only if it is still the
      # loop's sleep: gone or reused since the scan, nothing is killed.
      own= name= pp=
      while read -r key ids; do
        case $key in
          Name:) name=$ids ;;
          PPid:) pp=$ids ;;
          NSpid:) own=${ids##*[[:space:]]} ;;
        esac
      done 2>/dev/null < "/proc/$pid/status"
      [ "$name" = sleep ] || continue
      case $loops in *" $pp "*) ;; *) continue ;; esac
      [ -n "$own" ] && kill -KILL "$own" && killed=1 ;;
    esac
  done < "$HOME/ps"
  [ -n "$killed" ] && break
  sleep 0.1
done
[ -n "$killed" ] || { echo "no loop sleep found" >&2; exit 1; }
"""


class EntrypointCase(unittest.TestCase):
    """A temporary home, a stand-in claude, and the helpers every test here uses."""

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
        # A timeout, so a background sync that outlived the entrypoint fails
        # the test instead of hanging it on the inherited pipes.
        return subprocess.run(
            ["bash", str(ENTRYPOINT), *args],
            env=env or self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

    def git(self, *args: str) -> str:
        env = {"HOME": str(self.home), "PATH": os.environ["PATH"]}
        return subprocess.run(
            ["git", "config", *args], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()

    def token_file(self, body: str) -> dict[str, str]:
        path = self.root / "trentina-token"
        path.write_text(body)
        return {**self.env, "TRENTINA_MCP_TOKEN_FILE": str(path)}

    def assert_refused(self, env: dict[str, str]) -> None:
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("TRENTINA_MCP_TOKEN_FILE", result.stderr)
        self.assertNotIn("s3cret-token", result.stderr)
        self.assertNotIn("file-token", result.stderr)
        self.assertFalse((self.home / "claude-args").exists())
        self.assertFalse((self.home / ".claude.json").exists())


class EntrypointTest(EntrypointCase):
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

    def test_the_buildah_staging_dir_exists_under_storage(self) -> None:
        result = self.run_entrypoint()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".local/share/containers/storage/tmp").is_dir())

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

    def test_a_token_file_wins_over_the_variable(self) -> None:
        result = self.run_entrypoint(env=self.token_file("file-token\n"))
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.home / ".claude.json").read_text())
        self.assertEqual(state["mcpServers"]["trentina"]["headers"]["Authorization"], "Bearer file-token")
        self.assertEqual(
            self.git("--get", "http.http://192.0.2.2:8019/git/.extraHeader"),
            "Authorization: Bearer file-token",
        )
        self.assertIn(
            "OPENROUTER_API_KEY=file-token\n", (self.home / ".config/mcp-env/gatehouse.env").read_text()
        )
        claude_env = (self.home / "claude-env").read_text()
        self.assertNotIn("file-token", claude_env)
        self.assertNotIn("TRENTINA_MCP_TOKEN", claude_env)

    def test_a_token_file_works_without_the_variable(self) -> None:
        env = {k: v for k, v in self.token_file("file-token").items() if k != "TRENTINA_MCP_TOKEN"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads((self.home / ".claude.json").read_text())
        self.assertEqual(state["mcpServers"]["trentina"]["headers"]["Authorization"], "Bearer file-token")

    def test_a_missing_token_file_stops_the_start(self) -> None:
        self.assert_refused({**self.env, "TRENTINA_MCP_TOKEN_FILE": str(self.root / "nope")})

    def test_a_token_file_that_is_a_directory_stops_the_start(self) -> None:
        self.assert_refused({**self.env, "TRENTINA_MCP_TOKEN_FILE": str(self.root)})

    def test_a_token_file_with_a_nul_stops_the_start(self) -> None:
        self.assert_refused(self.token_file("file-token\0extra\n"))

    def test_an_oversized_token_file_stops_the_start(self) -> None:
        self.assert_refused(self.token_file("x" * 4097))

    def test_the_size_limit_is_in_bytes_in_a_multibyte_locale(self) -> None:
        env = {**self.token_file("\u00e9" * 2049), "LC_ALL": "C.UTF-8"}
        self.assert_refused(env)

    def test_a_token_file_at_the_size_limit_is_accepted(self) -> None:
        result = self.run_entrypoint(env=self.token_file("x" * 4095 + "\n"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_empty_token_file_stops_the_start(self) -> None:
        self.assert_refused(self.token_file(""))
        self.assert_refused(self.token_file("\n"))

    def test_a_token_file_of_more_than_one_line_stops_the_start(self) -> None:
        self.assert_refused(self.token_file("file-token\n\n"))
        self.assert_refused(self.token_file("file-token\r\n"))
        self.assert_refused(self.token_file("file-token\nsecond"))

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

    def _git(self, cwd: Path, *args: str) -> str:
        identity = ("-c", "user.name=t", "-c", "user.email=t@example.com")
        done = subprocess.run(
            ["git", *identity, *args], cwd=cwd, env=self.env, capture_output=True, text=True, check=True
        )
        return done.stdout.strip()

    def test_a_skills_checkout_on_a_feature_branch_is_moved_to_main(self) -> None:
        """A volume seeded on a feature branch never saw main again: every
        start pulled the branch's upstream, which did not move, and said so
        quietly. The seat ran three-week-old skills."""
        upstream = self.root / "upstream"
        (upstream / "skills/alpha").mkdir(parents=True)
        (upstream / "skills/alpha/SKILL.md").write_text("alpha\n")
        self._git(upstream, "init", "-q", "-b", "main")
        self._git(upstream, "add", "-A")
        self._git(upstream, "commit", "-qm", "alpha")
        self._git(upstream, "checkout", "-qb", "fix/old")
        self._git(upstream, "commit", "-qm", "old fix", "--allow-empty")
        shutil.rmtree(self.skills)
        self._git(self.root, "clone", "-q", "-b", "fix/old", str(upstream), str(self.skills))
        self._git(upstream, "checkout", "-q", "main")
        (upstream / "skills/gamma").mkdir()
        (upstream / "skills/gamma/SKILL.md").write_text("gamma\n")
        self._git(upstream, "add", "-A")
        self._git(upstream, "commit", "-qm", "gamma")

        self.run_entrypoint()

        self.assertEqual(self._git(self.skills, "branch", "--show-current"), "main")
        self.assertTrue((self.home / ".claude/skills/gamma").exists())

    def skills_upstream(self) -> Path:
        upstream = self.root / "upstream"
        (upstream / "skills/delta").mkdir(parents=True)
        (upstream / "skills/delta/SKILL.md").write_text("delta\n")
        self._git(upstream, "init", "-q", "-b", "main")
        self._git(upstream, "add", "-A")
        self._git(upstream, "commit", "-qm", "delta")
        return upstream

    # #42: a merge reaches a running seat without a restart.
    def test_the_background_sync_picks_up_a_skill_merged_while_running(self) -> None:
        upstream = self.skills_upstream()
        shutil.rmtree(self.skills)
        self._git(self.root, "clone", "-q", str(upstream), str(self.skills))
        # The stand-in Claude merges a skill upstream, then outlives two passes.
        claude = self.root / "bin/claude"
        claude.write_text(
            FAKE_CLAUDE
            + f"cd {upstream} && mkdir -p skills/omega && echo omega > skills/omega/SKILL.md\n"
            + "git -c user.name=t -c user.email=t@example.com add -A >/dev/null\n"
            + "git -c user.name=t -c user.email=t@example.com commit -qm omega\n"
            + "sleep 3\n"
        )
        result = self.run_entrypoint(env={**self.env, "JOSUI_SKILLS_SYNC_INTERVAL": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".claude/skills/omega").is_symlink())

    def test_a_sync_sleep_killed_from_outside_does_not_end_the_loop(self) -> None:
        """Under set -e the loop died silently when its sleep was killed; the
        next pass must run instead, and pick up what was merged."""
        upstream = self.skills_upstream()
        shutil.rmtree(self.skills)
        self._git(self.root, "clone", "-q", str(upstream), str(self.skills))
        claude = self.root / "bin/claude"
        claude.write_text(
            FAKE_CLAUDE
            + f"cd {upstream} && mkdir -p skills/omega && echo omega > skills/omega/SKILL.md\n"
            + "git -c user.name=t -c user.email=t@example.com add -A >/dev/null\n"
            + "git -c user.name=t -c user.email=t@example.com commit -qm omega\n"
            # The loop is the entrypoint's other child; kill only its sleep.
            # /proc and bash only: the python-sdlc image has no procps and no awk.
            + KILL_LOOP_SLEEP
            + "exec sleep 3\n"
        )
        result = self.run_entrypoint(env={**self.env, "JOSUI_SKILLS_SYNC_INTERVAL": "60"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".claude/skills/omega").is_symlink(), result.stderr)

    def test_term_stops_the_sync_loop_with_the_entrypoint(self) -> None:
        """The loop and its sleep must not outlive the container's main
        process: if either did, the pipes stay open and communicate() times
        out."""
        claude = self.root / "bin/claude"
        # exec, so TERM reaches the sleep itself and only a leaked loop could
        # keep the pipes open.
        claude.write_text(FAKE_CLAUDE + 'touch "$HOME/claude-up"\nexec sleep 60\n')
        proc = subprocess.Popen(
            ["bash", str(ENTRYPOINT)],
            env={**self.env, "JOSUI_SKILLS_SYNC_INTERVAL": "600"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(proc.kill)
        for _ in range(200):
            if (self.home / "claude-up").exists():
                break
            time.sleep(0.05)
        proc.terminate()
        proc.communicate(timeout=20)
        self.assertIsNotNone(proc.returncode)

    def test_a_bad_sync_interval_syncs_only_at_start(self) -> None:
        result = self.run_entrypoint(env={**self.env, "JOSUI_SKILLS_SYNC_INTERVAL": "10m"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("JOSUI_SKILLS_SYNC_INTERVAL is not a whole number", result.stderr)

    # An empty skills volume (Exedore max4, #42) is cloned, not left empty.
    def test_an_empty_skills_volume_is_cloned_and_linked(self) -> None:
        upstream = self.skills_upstream()
        shutil.rmtree(self.skills)
        self.skills.mkdir()
        result = self.run_entrypoint(env={**self.env, "JOSUI_SKILLS_URL": str(upstream)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._git(self.skills, "branch", "--show-current"), "main")
        self.assertEqual([p.name for p in (self.home / ".claude/skills").iterdir()], ["delta"])

    def test_a_skills_clone_that_fails_is_reported_not_fatal(self) -> None:
        shutil.rmtree(self.skills)
        self.skills.mkdir()
        result = self.run_entrypoint(env={**self.env, "JOSUI_SKILLS_URL": str(self.root / "no-such-repo")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skills clone", result.stderr)
        self.assertEqual(list((self.home / ".claude/skills").iterdir()), [])

    # A volume with files and no .git is someone's, not ours to clone over.
    def test_a_skills_dir_that_is_not_a_checkout_is_left_alone(self) -> None:
        upstream = self.skills_upstream()
        self.run_entrypoint(env={**self.env, "JOSUI_SKILLS_URL": str(upstream)})
        self.assertFalse((self.skills / ".git").exists())
        self.assertEqual(sorted(p.name for p in (self.home / ".claude/skills").iterdir()), ["alpha", "beta"])

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

    def fake_getent(self, address: str) -> pathlib.Path:
        """A stand-in getent that resolves every name to `address`, and logs the call."""
        fake = pathlib.Path(self.env["PATH"].split(os.pathsep)[0]) / "getent"
        body = '[ -n "$1" ] || exit 2\necho "$@" >> "$HOME/getent-calls"\n'
        if address:
            body += f'echo "{address}      STREAM $2"\n'
        else:
            body += "exit 2\n"
        fake.write_text("#!/bin/bash\n" + body)
        fake.chmod(0o755)
        return self.home / "getent-calls"

    def gatehouse_url_for(self, mcp_url: str) -> tuple[str, str]:
        env = {**self.env, "TRENTINA_MCP_URL": mcp_url}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.home / ".config/mcp-env/gatehouse.env").read_text()
        line = next(x for x in body.splitlines() if x.startswith("GATEHOUSE_LLM_BASE_URL="))
        return line.split("=", 1)[1], result.stderr

    def test_a_named_gateway_on_plain_http_gets_its_private_address(self) -> None:
        """Gatehouse refuses a name over plain http (#89), so it gets the address."""
        self.fake_getent("10.89.0.50")
        url, stderr = self.gatehouse_url_for("http://trentina:8019/gateway/agent1/mcp")
        self.assertEqual(url, "http://10.89.0.50:8019/proxy/llm/openrouter-gatehouse/v1")
        self.assertIn("resolves to 10.89.0.50", stderr)

    def test_a_named_gateway_resolving_public_is_left_for_gatehouse_to_refuse(self) -> None:
        """Pinning a public address would hand plaintext a pass Gatehouse denies."""
        self.fake_getent("203.0.113.7")
        url, stderr = self.gatehouse_url_for("http://trentina:8019/gateway/agent1/mcp")
        self.assertEqual(url, "http://trentina:8019/proxy/llm/openrouter-gatehouse/v1")
        self.assertIn("resolves to '203.0.113.7', not a private address", stderr)

    def test_a_named_gateway_that_does_not_resolve_is_left_alone(self) -> None:
        self.fake_getent("")
        url, stderr = self.gatehouse_url_for("http://trentina:8019/gateway/agent1/mcp")
        self.assertEqual(url, "http://trentina:8019/proxy/llm/openrouter-gatehouse/v1")
        self.assertIn("resolves to 'nothing'", stderr)

    def test_https_and_literal_addresses_are_not_resolved(self) -> None:
        calls = self.fake_getent("10.0.0.1")
        for mcp_url, expected in (
            (
                "https://trentina.example.com/gateway/agent1/mcp",
                "https://trentina.example.com/proxy/llm/openrouter-gatehouse/v1",
            ),
            (
                "http://192.168.1.5:8019/gateway/agent1/mcp",
                "http://192.168.1.5:8019/proxy/llm/openrouter-gatehouse/v1",
            ),
            (
                "http://localhost:8019/gateway/agent1/mcp",
                "http://localhost:8019/proxy/llm/openrouter-gatehouse/v1",
            ),
        ):
            url, _ = self.gatehouse_url_for(mcp_url)
            self.assertEqual(url, expected)
        self.assertFalse(calls.exists())

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

    # JOSUI_MODE=worker: a named interactive Claude Code in tmux `dev`, no
    # Remote Control server; JOSUI_RC=1 turns Remote Control on for it.
    def worker_calls(self, **extra: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        self.fake_tmux()
        env = {
            **self.env,
            "JOSUI_MODE": "worker",
            "JOSUI_NAME": "Vertex 1",
            "JOSUI_PERMISSION_MODE": "bypassPermissions",
            **extra,
        }
        result = self.run_entrypoint(env=env)
        return result, (self.home / "tmux-args").read_text().splitlines()

    def test_worker_runs_a_named_session_in_tmux_without_the_server(self) -> None:
        result, calls = self.worker_calls()
        # The fake reports no session, so the watcher returns and the
        # container ends -- which is what a unit restart relies on.
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls[0],
            f"new-session -d -s dev -x 200 -y 50 -c {self.home / 'work'} claude-worker -n Vertex 1 "
            "--permission-mode bypassPermissions",
        )
        self.assertIn("set-option -t dev remain-on-exit on", calls)
        self.assertIn("has-session -t dev", calls)
        self.assertFalse((self.home / "claude-args").exists())
        self.assertIn("worker 'Vertex 1' is up", result.stderr)

    def test_worker_pane_is_restarted_when_its_program_ends(self) -> None:
        _, calls = self.worker_calls()
        self.assertIn(
            "set-hook -t dev pane-died run-shell -b 'sleep 10; tmux respawn-pane -t #{hook_pane}'", calls
        )

    def test_worker_with_remote_control_names_it(self) -> None:
        _, calls = self.worker_calls(JOSUI_NAME="Max 1", JOSUI_RC="1")
        self.assertIn(
            "claude-worker -n Max 1 --remote-control Max 1 --permission-mode bypassPermissions",
            calls[0],
        )

    def test_worker_answers_the_first_run_questions(self) -> None:
        self.worker_calls()
        data = json.loads((self.home / ".claude.json").read_text())
        self.assertTrue(data["hasCompletedOnboarding"])
        self.assertTrue(data["remoteDialogSeen"])
        self.assertTrue(data["projects"][str(self.home / "work")]["hasTrustDialogAccepted"])

    def test_worker_does_not_also_start_the_josui_tmux_session(self) -> None:
        _, calls = self.worker_calls(JOSUI_TMUX="1")
        self.assertEqual(sum(c.startswith("new-session") for c in calls), 1)

    def test_worker_without_tmux_fails_loudly(self) -> None:
        env = {**self.env, "JOSUI_MODE": "worker", "JOSUI_TMUX_CMD": "tmux-that-is-not-installed"}
        result = self.run_entrypoint(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs tmux-that-is-not-installed", result.stderr)

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

    # claude-worker, the program in a worker's pane. It pins one
    # session UUID per seat in ~/.config/josui/session-id: --resume it when its
    # transcript holds a user turn, else --session-id (a fresh conversation).
    # Claude Code refuses both flags, exit 1, on a transcript with no
    # conversation in it, so such a transcript gets a new UUID instead.
    SEAT_ARGS = ("-n", "Max 1", "--remote-control", "Max 1", "--permission-mode", "bypassPermissions")
    PINNED = "0c9e5d2a-6f1b-4c7e-9a3d-2b8f4e6a1c55"
    USER_TURN = '{"type":"user","message":{"role":"user","content":"hi"}}\n'

    def worker_claude(self, cwd: Path | None = None) -> list[str]:
        cwd = cwd or self.home / "work"
        cwd.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                str(WORKER_CLAUDE),
                "-n",
                "Max 1",
                "--remote-control",
                "Max 1",
                "--permission-mode",
                "bypassPermissions",
            ],
            env=self.env,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.home / "claude-args").read_text().splitlines()

    def pin(self, session: str = PINNED) -> None:
        state = self.home / ".config/josui"
        state.mkdir(parents=True, exist_ok=True)
        (state / "session-id").write_text(session + "\n")

    def pinned(self) -> str:
        return (self.home / ".config/josui/session-id").read_text().strip()

    def transcript(self, session: str = PINNED, cwd: Path | None = None) -> Path:
        cwd = (cwd or self.home / "work").resolve()
        project = (
            self.home
            / ".claude/projects"
            / "".join(c if c.isascii() and c.isalnum() else "-" for c in str(cwd))
        )
        project.mkdir(parents=True, exist_ok=True)
        return project / f"{session}.jsonl"

    def assert_fresh(self, args: list[str], session: str) -> None:
        self.assertEqual(
            args,
            [
                "--session-id",
                session,
                "-n",
                "Max 1",
                "--remote-control",
                "Max 1",
                "--permission-mode",
                "bypassPermissions",
            ],
        )

    def test_a_first_start_pins_a_new_session_and_starts_it(self) -> None:
        args = self.worker_claude()
        session = self.pinned()
        self.assertRegex(session, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assert_fresh(args, session)

    def test_the_pinned_session_is_resumed_under_the_same_name(self) -> None:
        self.pin()
        self.transcript().write_text('{"type":"mode"}\n' + self.USER_TURN)
        self.assertEqual(
            self.worker_claude(),
            [
                "--resume",
                self.PINNED,
                "-n",
                "Max 1",
                "--remote-control",
                "Max 1",
                "--permission-mode",
                "bypassPermissions",
            ],
        )

    # A pane that died before its first message wrote no transcript: the same
    # UUID is still free, and kept.
    def test_a_pinned_session_with_no_transcript_yet_is_started_again(self) -> None:
        self.pin()
        self.assert_fresh(self.worker_claude(), self.PINNED)
        self.assertEqual(self.pinned(), self.PINNED)

    def test_an_empty_transcript_gets_a_new_session(self) -> None:
        self.pin()
        self.transcript().write_text("")
        args = self.worker_claude()
        self.assertNotEqual(self.pinned(), self.PINNED)
        self.assert_fresh(args, self.pinned())

    def test_a_corrupt_transcript_gets_a_new_session(self) -> None:
        self.pin()
        self.transcript().write_text('not json{\n{"type":"user"\n')
        args = self.worker_claude()
        self.assertNotEqual(self.pinned(), self.PINNED)
        self.assert_fresh(args, self.pinned())

    def test_a_transcript_with_no_user_turn_gets_a_new_session(self) -> None:
        self.pin()
        self.transcript().write_text('{"type":"custom-title","customTitle":"Max 1"}\n')
        args = self.worker_claude()
        self.assertNotEqual(self.pinned(), self.PINNED)
        self.assert_fresh(args, self.pinned())

    def test_a_garbled_pin_is_replaced(self) -> None:
        self.pin("../../etc/passwd")
        args = self.worker_claude()
        self.assertRegex(self.pinned(), r"^[0-9a-f-]{36}$")
        self.assert_fresh(args, self.pinned())

    # A pin that cannot be saved must not stop the pane from starting.
    def test_an_unwritable_state_dir_still_starts_claude(self) -> None:
        (self.home / ".config").mkdir()
        (self.home / ".config/josui").write_text("not a directory\n")
        args = self.worker_claude()
        self.assertEqual(args[0], "--session-id")
        self.assertRegex(args[1], r"^[0-9a-f-]{36}$")

    def test_saving_the_pin_leaves_no_temporary_file(self) -> None:
        self.worker_claude()
        self.assertEqual([p.name for p in (self.home / ".config/josui").iterdir()], ["session-id"])

    # Someone ran claude by hand in ~/work: its newer conversation is not the
    # seat's, with a pin or without one.
    def test_a_hand_run_conversation_is_never_taken_over(self) -> None:
        hand_run = "11111111-2222-4333-8444-555555555555"
        self.transcript(hand_run).write_text(self.USER_TURN)
        args = self.worker_claude()
        self.assertNotIn(hand_run, args)
        self.assertNotIn("--continue", args)
        self.pin()
        self.transcript().write_text(self.USER_TURN)
        self.assertEqual(self.worker_claude()[:2], ["--resume", self.PINNED])

    # Claude Code files a transcript under the physical cwd.
    def test_a_symlinked_cwd_finds_the_transcript_under_its_target(self) -> None:
        real = self.root / "real-work"
        real.mkdir()
        link = self.home / "work"
        link.symlink_to(real)
        self.pin()
        self.transcript(cwd=real).write_text(self.USER_TURN)
        self.assertEqual(self.worker_claude(link)[:2], ["--resume", self.PINNED])

    def test_a_cwd_with_punctuation_maps_to_claude_codes_project_dir(self) -> None:
        cwd = self.home / "my_work.d"
        self.pin()
        self.transcript(cwd=cwd).write_text(self.USER_TURN)
        self.assertEqual(self.worker_claude(cwd)[:2], ["--resume", self.PINNED])

    # The Bypass Permissions acceptance lives in settings.json, which is
    # replaced from the mount at every start.
    # ssh for a Dolza admin seat (constitution 3.0.0).
    def fake_ssh(self) -> Path:
        ssh = self.root / "bin/ssh"
        ssh.write_text("#!/bin/bash\nexit 0\n")
        ssh.chmod(0o755)
        return ssh

    def seed_ssh_mount(self, *, known_hosts: bool = True, key: bool = True) -> Path:
        src = self.root / "seat-ssh"
        src.mkdir()
        if key:
            # The mount is the host's: a loose mode is what to expect.
            (src / "id_ed25519").write_text("PRIVATE KEY\n")
            (src / "id_ed25519").chmod(0o644)
        (src / "config").write_text("Host gloval\n  Port 22422\n")
        if known_hosts:
            (src / "known_hosts").write_text("gloval ssh-ed25519 AAAA\n")
        return src

    def ssh_env(self, src: Path, runtime: Path | None = None) -> dict[str, str]:
        if runtime is None:
            # /dev/shm is tmpfs on every Linux, CI runners included.
            shm = tempfile.mkdtemp(dir="/dev/shm")
            self.addCleanup(shutil.rmtree, shm, True)
            runtime = Path(shm) / "tmpfs-ssh"
        self.ssh_runtime = runtime
        return {**self.env, "JOSUI_SSH_DIR": str(src), "JOSUI_SSH_RUNTIME": str(runtime)}

    def test_a_runtime_that_cannot_be_created_is_reported_not_fatal(self) -> None:
        self.fake_ssh()
        blocker = self.root / "blocker"
        blocker.write_text("a file, not a directory\n")
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount(), runtime=blocker / "ssh"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NOT installed", result.stderr)

    def test_a_runtime_under_home_is_refused(self) -> None:
        self.fake_ssh()
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount(), runtime=self.home / "keys"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NOT installed", result.stderr)
        self.assertEqual(list(self.home.rglob("id_ed25519")), [])

    def test_a_runtime_that_is_not_tmpfs_is_refused(self) -> None:
        disk = self.root / "disk-ssh"
        if (
            subprocess.run(
                ["stat", "-f", "-c", "%T", str(self.root)], capture_output=True, text=True
            ).stdout.strip()
            == "tmpfs"
        ):
            self.skipTest("the scratch directory is itself on tmpfs here")
        self.fake_ssh()
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount(), runtime=disk))
        self.assertIn("NOT installed", result.stderr)
        self.assertFalse((disk / "id_ed25519").exists())

    def test_no_ssh_identity_unless_one_is_mounted(self) -> None:
        self.fake_ssh()
        result = self.run_entrypoint(env={**self.env, "JOSUI_SSH_DIR": str(self.root / "absent")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / ".ssh").exists())

    def test_a_mounted_identity_lands_in_tmpfs_never_in_home(self) -> None:
        self.fake_ssh()
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount()))
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = self.ssh_runtime
        self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o700)
        for name in ("id_ed25519", "config", "known_hosts"):
            self.assertEqual(stat.S_IMODE((runtime / name).stat().st_mode), 0o600, name)
        self.assertEqual((runtime / "id_ed25519").read_text(), "PRIVATE KEY\n")
        # ~/.ssh is a link to tmpfs: nothing of the key is in the $HOME volume.
        self.assertTrue((self.home / ".ssh").is_symlink())
        self.assertEqual((self.home / ".ssh").resolve(), runtime.resolve())
        self.assertEqual(list(self.home.rglob("id_ed25519")), [])

    def test_a_real_ssh_dir_in_home_is_set_aside_not_deleted(self) -> None:
        self.fake_ssh()
        (self.home / ".ssh").mkdir(parents=True)
        (self.home / ".ssh/note").write_text("kept\n")
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".ssh").is_symlink())
        kept = list(self.home.glob(".ssh.before-tmpfs.*"))
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0] / "note").read_text(), "kept\n")

    def test_an_ssh_mount_without_a_key_is_reported_not_fatal(self) -> None:
        self.fake_ssh()
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount(key=False)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no private key", result.stderr)
        self.assertFalse((self.home / ".ssh").exists())

    def test_a_key_without_known_hosts_is_called_out(self) -> None:
        self.fake_ssh()
        result = self.run_entrypoint(env=self.ssh_env(self.seed_ssh_mount(known_hosts=False)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no known_hosts", result.stderr)

    def test_an_ssh_identity_with_no_ssh_in_the_image_is_reported_not_fatal(self) -> None:
        env = {**self.ssh_env(self.seed_ssh_mount()), "JOSUI_SSH_CMD": "ssh-that-is-not-installed"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("there is no ssh-that-is-not-installed in this image", result.stderr)
        self.assertFalse((self.home / ".ssh").exists())

    def test_a_bypass_acceptance_survives_a_restart(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        settings = self.home / ".claude/settings.json"
        data = json.loads(settings.read_text())
        data["skipDangerousModePermissionPrompt"] = True
        settings.write_text(json.dumps(data))
        (self.config / "settings.json").write_text('{"spinnerTipsEnabled": false}\n')
        self.assertEqual(self.run_entrypoint().returncode, 0)
        data = json.loads(settings.read_text())
        self.assertIs(data["skipDangerousModePermissionPrompt"], True)
        self.assertIs(data["spinnerTipsEnabled"], False)

    def test_the_entrypoint_never_accepts_bypass_itself(self) -> None:
        self.assertEqual(self.run_entrypoint().returncode, 0)
        self.assertEqual(self.run_entrypoint().returncode, 0)
        data = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertNotIn("skipDangerousModePermissionPrompt", data)

    # A worker with a homeserver is ONE session, held by claude-agent.
    def test_texted_worker_is_one_session_under_claude_agent(self) -> None:
        self.fake_tmux()
        env = {**self.session_env(), "JOSUI_MODE": "worker", "JOSUI_RC": "1", "JOSUI_NAME": "Josui Prime"}
        result = self.run_entrypoint(env=env)
        # The stand-in claude-agent exits 7: the container ends with it.
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(
            (self.home / "agent-args").read_text().splitlines(),
            [
                "run",
                "--",
                "-n",
                "Josui Prime",
                "--remote-control",
                "Josui Prime",
                "--permission-mode",
                "bypassPermissions",
            ],
        )
        # No Remote Control server and no second Claude Code.
        self.assertFalse((self.home / "claude-args").exists())
        self.assertIn("under claude-agent with texting", result.stderr)
        self.assertEqual(self.agent_env()["CLAUDE_AGENT_MATRIX_HOMESERVER"], "http://192.0.2.3:6167")

    # The supervisor runs claude-worker, so the texted worker
    # continues its conversation at every (re)spawn as a seat does.
    def test_texted_worker_continues_through_the_wrapper(self) -> None:
        self.fake_tmux()
        self.run_entrypoint(env={**self.session_env(), "JOSUI_MODE": "worker", "JOSUI_NAME": "Josui Prime"})
        self.assertEqual(self.agent_env()["CLAUDE_AGENT_CLAUDE"], "claude-worker")

    def test_text_session_beside_the_server_runs_plain_claude(self) -> None:
        self.run_entrypoint(env=self.session_env())
        self.assertNotIn("CLAUDE_AGENT_CLAUDE", self.agent_env())

    # claude-agent holds the session in tmux itself: a `dev` session running
    # its attach would nest tmux, so there is none, and the log says how in.
    def test_texted_worker_starts_no_tmux_and_says_how_to_attach(self) -> None:
        self.fake_tmux()
        env = {**self.session_env(), "JOSUI_MODE": "worker", "JOSUI_NAME": "Josui Prime"}
        result = self.run_entrypoint(env=env)
        self.assertFalse((self.home / "tmux-args").exists())
        self.assertIn("podman exec -it <service> claude-agent attach --rw", result.stderr)
        self.assertNotIn("tmux attach -t dev", result.stderr)

    def test_worker_without_a_homeserver_is_not_supervised(self) -> None:
        agent = self.root / "bin/claude-agent"
        agent.write_text('#!/bin/bash\ntouch "$HOME/agent-ran"\n')
        agent.chmod(0o755)
        result, calls = self.worker_calls()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "agent-ran").exists())
        self.assertIn("claude-worker -n Vertex 1", calls[0])

    def test_texted_worker_without_remote_control_omits_it(self) -> None:
        self.fake_tmux()
        env = {**self.session_env(), "JOSUI_MODE": "worker", "JOSUI_NAME": "Josui Prime"}
        self.run_entrypoint(env=env)
        self.assertEqual(
            (self.home / "agent-args").read_text().splitlines(),
            ["run", "--", "-n", "Josui Prime", "--permission-mode", "bypassPermissions"],
        )

    def test_texted_worker_without_tmux_fails_loudly(self) -> None:
        env = {**self.session_env(), "JOSUI_MODE": "worker", "JOSUI_TMUX_CMD": "tmux-that-is-not-installed"}
        result = self.run_entrypoint(env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("JOSUI_MODE=worker needs tmux-that-is-not-installed", result.stderr)
        self.assertFalse((self.home / "agent-args").exists())


if __name__ == "__main__":
    unittest.main()
