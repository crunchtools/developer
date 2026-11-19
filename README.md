# claude

The crunchtools build of Claude Code in a container, published as
`quay.io/crunchtools/claude`. Josui Prime and the worker seats (Vertex 1-4 on
dolza, Max 1-4 on exedore) all run it; the workers **build** the crunchtools
fleet. Until #41's cutover is done the image is also published as
`quay.io/crunchtools/developer`, and the old `developer-*` command names are
links to the new `claude-*` ones.

It is not [josui](https://github.com/crunchtools/josui), and that is the point.
`josui.crunchtools.com` is one agent that *operates* the fleet: the operator
role on the Trentina gateway, its owner's texts, production reach. These three
have none of that, on purpose. What they have instead is a toolchain and a
container engine, so an agent can run a repository's own gates in seconds
rather than waiting for CI to render them.

## What goes in

| Mount | Container path | What it is |
|-------|----------------|------------|
| state | `/home/podman` | Claude login, sessions, clones, caches. The only writable home. |
| containers | `/home/podman/.local/share/containers` | The nested engine's storage. A **cache** — exclude it from backups. |
| identity | `/etc/josui` (read-only) | `CLAUDE.md`, `settings.json`. Says what this agent is and what it is not. |
| tool keys | `/etc/josui/mcp-env` (read-only) | `*.env` for a gate that takes an API key. Installed to `~/.config/mcp-env` at 0600. No gate needs one today: Gatehouse goes through the gateway. A `gatehouse.env` placed here is overwritten by the entrypoint, not obeyed. |
| skills | `/var/lib/josui/josui-skills` | A git checkout, kept on the default branch and fast-forwarded at start and every `JOSUI_SKILLS_SYNC_INTERVAL` seconds (600; `0` for start only), so a josui-skills merge goes live without a restart. A diverged checkout is never merged, only reported. An empty volume is cloned from `JOSUI_SKILLS_URL` (default `https://github.com/crunchtools/josui-skills`, through the gateway's git route). A read-only checkout (Dolza's shared one, in every seat but Josui Prime) is only relinked. Each pass over a checkout the seat can write records it in `.git/skills-sync.status` (on Dolza, Josui Prime's passes write the shared one), which [deploy/](deploy/) copies to pCloud for Nagios. |
| audit | `/var/log/josui` | One JSON line per tool call, plus Remote Control's debug file. |

No `/srv` service tree, no production data. Repositories are cloned through the
gateway's git route into the container's own volume.

The paths and the `JOSUI_*` environment prefix are inherited from josui, where
this image began as `Containerfile.dev`. They are a starting point, not a
commitment; renaming the prefix is its own change, with a window where both are
accepted, rather than a flag day bundled into a move.

## Network

An internal network with no DNS. Its only peer is the
[Trentina](https://github.com/crunchtools/trentina) gateway, by address: MCP on
`TRENTINA_MCP_URL`, Claude Code's own HTTPS through `HTTPS_PROXY` to an
allowlist, git through `TRENTINA_GIT_URL`. No forge host is on the allowlist;
the gateway holds the GitHub token and limits which repositories and branches a
push may move. The nested engine pulls through that same allowlist.

The seat's gateway token is `TRENTINA_MCP_TOKEN`, or the file named by
`TRENTINA_MCP_TOKEN_FILE` (one line; a trailing newline is stripped), which
wins. A `TRENTINA_MCP_TOKEN_FILE` that is missing, unreadable, empty, over 4096
bytes, holds a NUL or is more than one line stops the container rather than
starting it without a gateway.
The token is written to Claude Code's state file, git's include file and
`gatehouse.env`, all 0600, and removed from the environment before Claude Code
starts. It is the only secret the entrypoint reads; the Matrix token is already
a file (`JOSUI_MATRIX_TOKEN_FILE`) that claude-agent reads itself.

## Attaching

```bash
podman exec -it developer1.crunchtools.com tmux attach -t dev   # JOSUI_TMUX=1
```

Window 0 is Claude Code, window 1 a shell in the work directory. tmux follows
the attaching terminal's size and survives the disconnect, which is why it is
this and not `claude-agent attach`. The session is deliberately not watched:
quitting your own shell must not end the container. Nothing listens for an
inbound connection.

### Worker mode

`JOSUI_MODE=worker` replaces the Remote Control server with one named
interactive Claude Code (`claude -n "$JOSUI_NAME"`) in the same tmux session
`dev`: a worker that Josui Prime messages as a peer and a person attaches to.
`JOSUI_RC=1` turns Remote Control on for that conversation (the Max seats on
Exedore); the Vertex workers on Dolza cannot use it and leave it unset.
Onboarding, the Remote Control dialog and trust in `JOSUI_WORKDIR` are recorded
as answered, so the unit never stalls on a first-run question. A pane whose
program ends is restarted ten seconds later (a `pane-died` hook). Here the
tmux session *is* watched: the container ends when the session does.

Managed hooks keep `~/.claude/seat-state.json` for Josui Prime's /agents:
`state`, `owner` and `task` say whether the seat is busy and who drives it,
and `turn` says whether Claude Code is in a turn right now (`active` from
`turn_started`, `idle` from `turn_ended`). A peer's message or a background
task's result starts a turn like a typed prompt does, but leaves `state`,
`owner` and `task` alone. A turn a person interrupts with Esc reads `active`
for about a minute, until Claude Code's idle notification ends it; if someone
types in that minute, it stays `active` until the next turn ends.

The same events also go to the Ashigaru Desk through `ashigaru hook`, which
applies the same rules. The image sets `ASHIGARU_URL` to
`https://ashigaru.crunchtools.com`; the hook does nothing until the seat is
also given a key (`ASHIGARU_API_KEY_FILE`, or `ASHIGARU_API_KEY`). Both run
until the cutover retires seat-state.json. The Ashigaru RPM's `/ashigaru`
skill is linked into Claude Code's managed skills folder,
`/etc/claude-code/.claude/skills`, so every seat has it beside josui-skills.

A worker with `JOSUI_MATRIX_HOMESERVER` set is held by `claude-agent run`
instead: one session that the Matrix bridge texts into, Remote Control reaches
and `claude-agent send` prompts. claude-agent keeps it in tmux on a server of
its own, and there is no `dev` session; attach with

```bash
podman exec -it josui claude-agent attach --rw   # or, over ssh: claude-agent attach --rw
```

which follows your terminal's size; prefix d detaches. Leave off `--rw` to
watch read-only. Run it outside tmux: inside one, it would nest.

Josui Prime is a worker too: `JOSUI_RC=1`, `JOSUI_NAME="Josui Prime"`, with
none of the nested-podman flags and its SELinux label kept, since it dispatches
rather than builds.

One thing survives a restart that the copy-in would otherwise erase. A person's
acceptance of the Bypass Permissions warning (`skipDangerousModePermissionPrompt`
in `~/.claude/settings.json`) is carried over when the mounted `settings.json`
replaces the old one. The entrypoint keeps an acceptance but never gives one.

## ssh (Dolza admin seats)

Every container on Dolza -- Prime, Dolza (Vertex) 1-4, Dolza (Max) 1-4 -- has
its own ssh key to Breetai, Gloval, Dolza and Exedore (constitution 3.0.0).
The unit mounts the seat's key with the shared ssh config and known_hosts at
`JOSUI_SSH_DIR`; the entrypoint copies them to tmpfs (`JOSUI_SSH_RUNTIME`,
default `/tmp/josui-ssh`) and links `~/.ssh` there, so no key is ever written
to the `$HOME` volume. `ssh gloval`, `ssh exedore`, `ssh dolza`, `ssh breetai`
then work. Exedore's seats mount nothing and get nothing. Keys are granted and
revoked per seat with josui's `tools/seat-ssh/grant` and `revoke`.

## Running a repository's own gates

The point of this image is that `sdlc push` runs here in minutes instead of a
CI round trip. Measured on a trentina clone, 2026-11-06: **293s in a seat
against 416s on the laptop** for the same tier, the same night.

`sdlc` is on `PATH`. In a converted repo, one carrying `.sdlc/bin/sdlc`, it
runs that file, as the repo's hooks and CI do. Otherwise it reads the `rev:`
the repo pins for python-sdlc in
`.pre-commit-config.yaml`, clones that exact tag once into
`~/.cache/crunchtools-sdlc`, and execs its launcher — so a gate run by hand and
the same gate in CI are the same tool. It never guesses a version: python-sdlc's
own contract is that the `rev:` line fixes the launcher, the image and every
tool in it, so a wrapper that defaulted would hand back a verdict CI does not
share. Outside a repo, with no config, or with a rev that is not a plain tag, it
exits 2 — "could not run", in python-sdlc's own convention.

A seat needs four things before that tier can run at all. Each was a gate
reporting `COULD NOT RUN` — the one verdict that must never be worked around —
and each is now either done by the entrypoint or a documented step:

| Need | Where it comes from |
|------|---------------------|
| a git identity | `JOSUI_GIT_NAME` and `JOSUI_GIT_EMAIL`, set by the entrypoint only if nothing is configured already |
| Gatehouse's LLM | the entrypoint writes `~/.config/mcp-env/gatehouse.env` pointing at the gateway's `/proxy/llm/openrouter/v1` route, with this seat's `TRENTINA_MCP_TOKEN` as the key. No OpenRouter key lives in a seat (RT#1559) |
| loopback out of the proxy | `NO_PROXY` must list `127.0.0.1,localhost,::1`. podman passes `HTTP(S)_PROXY` into every nested gate container by default, so without it a test talking to its own local server is tunnelled to the gateway and refused |
| the constitution checkout | `sdlc`'s validator runs in the gate container with `HOME=/tmp`, so the gateway's `insteadOf` rewrite does not apply and its plain clone of `crunchtools/constitution` is refused by the egress proxy. Seed it once per inherited version, through the route that does work: `git clone --depth 1 -b <tag> https://github.com/crunchtools/constitution "$(podman volume inspect crunchtools-hook-cache --format '{{.Mountpoint}}')/constitution/<tag>"` |

pytest run straight in the seat (not through `sdlc`, whose gate container has
its own CPU and memory cap) gets at most four xdist workers for `-n auto`: the
image sets `PYTEST_XDIST_AUTO_NUM_WORKERS=4`, because one worker per host core
outgrows the seat's memcg and the OOM killer takes Claude down with it.

A gate that wants a host the gateway's allowlist does not carry fails here and
nowhere else, and that is the design working. trentina's benchmark corpus
(`huggingface.co`) is the known example.

## What this image costs, measured

Nested podman needs four things the production image does not have. Each was
measured on the host rather than assumed, and each is recorded in
`.specify/memory/constitution.md`:

| Setting | Why |
|---------|-----|
| `--cap-drop=all --cap-add=setuid,setgid,sys_chroot,chown,setfcap` | `--cap-drop=all` alone fails at `newuidmap`; setuid and setgid then fail unpacking a layer; `sys_chroot` fixes it; `chown` and `setfcap` are kept for layers that carry either. Five of podman's eleven, no `SYS_ADMIN`, no `DAC_OVERRIDE`, never `--privileged` |
| `--security-opt label=disable` | with labels on, a nested pull succeeds and a nested run fails with `crun: read from sync socket`. A local policy module cannot replace it: with dontaudit disabled the same run emits no AVC at all |
| `--security-opt unmask=/proc/*` | podman masks eight paths under `/proc`, and the kernel then refuses the nested runtime a fresh procfs |
| `--device /dev/fuse` | the nested storage driver |

The root filesystem still stays `--read-only`, and UID 1000 maps to an
unprivileged host range — never host root. With SELinux off for these
containers, that UID map is the file-access control.

## When no session appears

Read the `--debug-file` in the audit volume first. Nothing else will tell you,
and liveness is **not** monitorable from inside: Remote Control polls rather
than holding a connection, and the debug file does not grow while idle. A
watchdog on either signal restarts healthy containers — one was built on the
first and removed (josui 0.7.0). What is watched from outside is the gateway
outliving the agent's MCP client, which is the failure the agent genuinely
cannot report on itself.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The image is built by GitHub Actions only.

## License

AGPL-3.0-or-later
