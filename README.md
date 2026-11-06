# developer

Claude Code packaged as a container for agents that **build** the crunchtools
fleet. Three of them run on gloval as `developer1`, `developer2`, `developer3`.

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
| tool keys | `/etc/josui/mcp-env` (read-only) | `*.env` for the gates that take an API key. Installed to `~/.config/mcp-env` at 0600. Absent, the gates that need one say so. |
| skills | `/var/lib/josui/josui-skills` | A git checkout, pulled at start. |
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

## Attaching

```bash
podman exec -it developer1.crunchtools.com tmux attach -t dev   # JOSUI_TMUX=1
```

Window 0 is Claude Code, window 1 a shell in the work directory. tmux follows
the attaching terminal's size and survives the disconnect, which is why it is
this and not `claude-agent attach`. The session is deliberately not watched:
quitting your own shell must not end the container. Nothing listens for an
inbound connection.

## Running a repository's own gates

The point of this image is that `sdlc push` runs here in minutes instead of a
CI round trip. Measured on a trentina clone, 2026-11-06: **293s in a seat
against 416s on the laptop** for the same tier, the same night.

`sdlc` is on `PATH`. It reads the `rev:` the repo pins for python-sdlc in
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
| Gatehouse's API key | an `*.env` file in the `/etc/josui/mcp-env` mount |
| loopback out of the proxy | `NO_PROXY` must list `127.0.0.1,localhost,::1`. podman passes `HTTP(S)_PROXY` into every nested gate container by default, so without it a test talking to its own local server is tunnelled to the gateway and refused |
| the constitution checkout | `sdlc`'s validator runs in the gate container with `HOME=/tmp`, so the gateway's `insteadOf` rewrite does not apply and its plain clone of `crunchtools/constitution` is refused by the egress proxy. Seed it once per inherited version, through the route that does work: `git clone --depth 1 -b <tag> https://github.com/crunchtools/constitution "$(podman volume inspect crunchtools-hook-cache --format '{{.Mountpoint}}')/constitution/<tag>"` |

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
