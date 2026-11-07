# developer Constitution

> **Version:** 1.5.0
> **Ratified:** 2026-11-06
> **Status:** Active
> **Inherits:** [crunchtools/constitution](https://github.com/crunchtools/constitution) v1.31.0
> **Profile:** Autonomous Agent

Claude Code packaged as a container for agents that **build** the crunchtools
fleet. Three instances run on gloval as developer1-3, sharing one Trentina
profile, each with its own clones and its own state.

It is not [josui](https://github.com/crunchtools/josui), and the difference is
the point: josui.crunchtools.com is one agent that OPERATES the fleet — the
operator role on the gateway, the owner's texts, production reach. These three
have none of that. The image began as `Containerfile.dev` in that repo and
inherits its entrypoint, its mount contract and its `JOSUI_*` environment
prefix; that inheritance is a starting point, not a commitment, and renaming
the prefix is its own change with a window where both are accepted.

This file holds what is specific to this deployment. The fleet rules and the
Autonomous Agent profile apply at the inherited version and are checked
against this repo's files by `constitution.yml`. They are not restated here.

## Trust Boundary

Two boundaries, both outside the model. On disk, the container can reach what
is bind-mounted into it and nothing else — and what is mounted is only its own
state, its own nested image cache, its audit log and the skills checkout. **No
`/srv` service tree and no production data**, which is what separates this
image from josui's at the deployment level rather than in prose: a task that
needs production belongs to Josui Prime.

On the network it sits on an internal network with no DNS (10.89.15.0/24)
whose only other member is the Trentina gateway. Every MCP tool result is
judged by the gateway before the agent reads it, and Claude Code's own HTTPS
leaves through the gateway's client egress to an allowlist. GitHub is not on
that allowlist: the API is the gateway's GitHub tools and git is its git route,
which holds the token and limits the repositories and branches a push may move.
The container holds no `gh` and no GitHub credential.

The nested container engine does not widen either boundary. It pulls through
the same egress allowlist, and what it runs is confined by the same container
the agent is in.

## Layer 1 — Trust Boundary Architecture

Three identical agents behind one gateway profile. The gateway is the
deterministic enforcer and holds the quarantined reader; the agent never
fetches from the web itself (`WebFetch` and `WebSearch` are denied in its
settings). Attended mode: each acts only when the operator opens a conversation
over Remote Control, or types into its tmux session. There is **no Matrix
channel and no text path** — texts reach Josui Prime, which is the right front
door for an agent that can act on production.

One profile for three containers is a deliberate trade, made by the owner: the
gateway's audit cannot tell them apart. Their source addresses differ, so the
distinction exists at the perimeter if it is ever needed.

## Layer 2 — MCP Server Governance

The `claude-agent-trentina` package's managed settings allow one MCP server,
`trentina`, by name, and turn off claude.ai connectors. Which backends and
tools the `developer` profile holds is the gateway's configuration and is not
restated here, beyond what it must not have: **no operator role**, so an agent
in this image cannot reconfigure the gateway it depends on, and no mail,
calendar or publishing backends. The entrypoint writes that one server into
Claude Code's state from `TRENTINA_MCP_URL` and `TRENTINA_MCP_TOKEN` at every
start, replacing whatever was there.

## Layer 3 — Container & Supply Chain Security

- **Image:** `quay.io/crunchtools/developer` and `ghcr.io/crunchtools/developer`,
  public. Tooling only; identity and credentials are mounted.
- **Base:** `quay.io/podman/stable`, pinned by digest, the same digest
  gha-runner runs. **Not UBI**: UBI 10 fails a nested `podman run` with "mount
  `proc` to `proc`: Operation not permitted" — measured in gha-runner's
  Containerfile against crun versions, storage drivers, every userns and netns
  combination, and `--privileged`.
- **Claude Code** (`CLAUDE_CODE_VERSION`) comes from Anthropic's signed
  repository and the claude-agent packages from crunchtools', both pinned and
  bumped by hand, because a new Claude Code is tried on Josui Prime before a
  development agent runs on it. `DISABLE_AUTOUPDATER=1`; telemetry and error
  reporting off.
- **Trivy** blocks on CRITICAL and HIGH with `.trivyignore`; SBOM (SPDX) per
  build. It does **not** cover this image's OS layer: Trivy reports
  `Unsupported os family="fedora"` and scans language packages only. `dnf -y
  upgrade` in the Containerfile plus the weekly `parent-image-updated` rebuild
  is that layer's control, and it is the only one.
- **No `schedule:` trigger** and no tag release: this is a build machine for
  agents, not a shipped artifact, so it tracks main and the fleet's pulse.
- **Runtime:** UID 1000 mapped to an **unprivileged host range**, never to host
  root; `--read-only` with tmpfs on `/tmp`, `/var/tmp` and `/run`; the nested
  engine's storage on a volume of its own; an internal network with
  `--dns=none`; memory and pid limits. It does **not** set `no-new-privileges`:
  `newuidmap` acquires its privilege through a file capability, which that
  option blocks.
- **Capabilities, measured on gloval 2026-11-05 (RT#1556):** `--cap-drop=all`
  plus `setuid`, `setgid`, `sys_chroot`, `chown`, `setfcap`. `--cap-drop=all`
  alone fails at `newuidmap`; `setuid` and `setgid` get past it and then fail
  unpacking a layer; `sys_chroot` is the one that fixes it; `chown` and
  `setfcap` are kept because a real layer carrying a file owner or a file
  capability needs them. Five of podman's eleven defaults, with no `SYS_ADMIN`,
  no `DAC_OVERRIDE`, no `MKNOD` and never `--privileged`.

## Layer 4 — Runtime Security & Behavioral Controls

Circuit breakers, audit and the prompts-off hook are claude-agent's, at the
settings the unit passes: tool calls per conversation, identical calls in a
row, one JSON line per tool call in the audit volume. Every Claude Code process
in the container runs with `CLAUDE_AGENT=1`, so the session a person types into
over tmux is counted and logged like the one Remote Control drives.

**Liveness is not monitorable from inside, and nothing should pretend
otherwise.** Remote Control polls rather than holding a connection — a
connection to the egress roughly every thirty to two hundred seconds, gone in
about a second — and its debug file does not grow while idle. A watchdog built
on either signal restarts healthy containers; one was built on the first and
removed (josui 0.7.0, RT#1556). A session that does not appear is diagnosed
from the `--debug-file`, not inferred.

## Layer 5 — Credential & Identity Management

The agent needs one credential: the gateway's bearer token, from the env file.
It is what the gateway identifies this seat by.

A second one is on its way out — an OpenRouter API key for
Gatehouse, the gate that reviews code with an LLM, mounted at
`/etc/josui/mcp-env` because `envscrub` would strip it from the environment
and the gate would then die reporting that it could not run.
`developer-entrypoint` writes `~/.config/mcp-env/gatehouse.env` itself when
the mount provides none, pointing Gatehouse at the gateway's
`/proxy/llm/openrouter` route with this seat's own token as the key. The URL
is derived from `TRENTINA_MCP_URL` and is not settable on its own, because
anything able to set it could redirect the token that file carries.

Gatehouse calls `/proxy/llm/openrouter-gatehouse/v1`, which is the gateway's
second route to the same upstream and carries its own key. That split is not
cosmetic: `llm_keys` is both the L3 defense judge's credential and what the
proxy injects, so one provider per profile billed judging and Gatehouse to the
same key. A seat therefore has two token endpoints, and all Gatehouse usage —
seats, breetai, CI, Ashigaru — lands on one key.

A mounted `gatehouse.env` wins, so deleting it is the cutover and re-placing
it is the rollback. That is deliberate and not a convenience: a seat pushes
many repos, each pins its own python-sdlc rev, and the Gatehouse a gate runs
is the PUSHED repo's choice. This image cannot raise that floor for anything
but itself, and Gatehouse below 0.24.0 ignores `GATEHOUSE_LLM_BASE_URL` and
would send the gateway token to openrouter.ai as an OpenRouter key. A seat
with no gateway token or URL also leaves the mount alone, because failing
there would take away the only Gatehouse such a seat could run.

Removing the mounted key removes the one credential that lives outside the
gateway. Its route never was — `openrouter.ai` is on this profile's egress
allowlist and the proxy carries the traffic — but the key is a second,
spendable copy of one the gateway already holds, with no per-seat accounting
and no revocation short of editing files on the host. The `openrouter.ai`
grant comes off this profile's allowlist with it, and until both happen Layer
5 names two credentials rather than one (RT#1559).

Gatehouse sends `provider: {"zdr": true, "data_collection": "deny"}` on every
request, because the diff it reviews is private code. Through the gateway that
guarantee is the gateway's to keep: `provider` is an allowed key on its
OpenRouter shape and the request body is forwarded verbatim. If that shape
narrows, this route stops being safe to use.

Everything else is the gateway's — the GitHub token on its git route and in its
GitHub tools, the LLM key for judging. The Claude login is personal, lives in
the state volume, and is established once, interactively, per container;
Remote Control refuses long-lived tokens, and one credential shared between
containers would have them invalidate each other's refresh token.

Identity (`CLAUDE.md`, `settings.json`) is mounted read-only from the host and
copied in at each start. It states what the agent is and what it is not; an
agent in this image that is handed another agent's identity will act as that
agent, which is a configuration error, not a cosmetic one.

## Layer 6 — Monitoring, Detection & Response

The audit volume is the record of what the agent did. What is watched from
outside is the failure the agent cannot report on itself: when the gateway
restarts, every client's MCP session dies with it, the gateway logs "client
predates a gateway restart", and the agent carries on with no tools, looking
healthy from every angle. `check_agent_gateway_stale.sh` in
crunchtools/nagios-agent compares the two containers' start times and says so.

## Deviations

- **SELinux labelling off.** The profile requires SELinux enforcing with
  labelled mounts; these containers run `--security-opt label=disable`, which
  is unconfined for container purposes. It is required: with labels on, a
  nested pull succeeds and a nested run fails with `crun: read from sync
  socket: Connection reset by peer`. A local policy module is **not** the fix
  in waiting — with dontaudit disabled (`semodule -DB`) the same run fails
  identically and emits no AVC at all, so there is nothing to write one from.

  What does SELinux's old job, in order of weight: UID 1000 mapped to an
  unprivileged host range, so file access is DAC's problem; only its own
  volumes mounted, no `/srv` tree and no production data; `--cap-drop=all` plus
  the five measured capabilities; `--read-only` with tmpfs; its own internal
  network with no DNS and no host route; egress only through its own gateway
  profile; memory and pid limits; and claude-agent's audit and breakers.

  What is lost, plainly: a path the container should not reach is stopped by
  the UID map alone, with no MAC behind it.

- **`/proc` unmasked.** `--security-opt unmask=/proc/*`. Podman masks eight
  paths under `/proc`, and the kernel then refuses the nested runtime a fresh
  procfs. `/sys/firmware`, `/sys/fs/selinux` and `/sys/dev/block` stay masked;
  `unmask=ALL` also works and gives away more than this needs.

- **System-tier tools.** The profile prohibits shell and file write in
  production. They are what this agent is for. It is contained by having no
  production to reach, not by removing them.

## Service

| Attribute | Value |
|-----------|-------|
| Container names | `developer1.crunchtools.com`, `developer2`, `developer3` |
| Port | none — nothing listens for an inbound connection |
| Host directory | `/srv/<service>/` (`config/` identity and env, `data/state`, `data/containers`, `data/audit`, `data/josui-skills`) |
| Interface | Claude Code Remote Control, and tmux over `podman exec` |

## History

| Version | Date | Changes |
|---------|------|---------|
| 1.5.0 | 2026-11-07 | Gatehouse calls `/proxy/llm/openrouter-gatehouse/v1`, a second gateway route to the same upstream carrying its own key, so a seat has two token endpoints: one the L3 judge spends on and one Gatehouse does. All Gatehouse usage then lands on one key fleet-wide. RT#1559 |
| 1.4.0 | 2026-11-07 | The gateway's `/proxy/llm/openrouter` route on the seat's own token is available to Gatehouse: the entrypoint writes that `gatehouse.env` when the mount provides none. A mounted key still wins, so deleting it is the cutover and the image deploys inert -- the Gatehouse floor is set by the PUSHED repo's python-sdlc pin, which this image cannot raise. Verified end to end on developer3 with Gatehouse 0.24.0. RT#1559 |
| 1.3.0 | 2026-11-06 | The GHCR mirror pushes once the package grants this repo access, so the continue-on-error guard comes off; `permissions.defaultMode` measured redundant against the unit's `JOSUI_PERMISSION_MODE` and not shipped. RT#1556 |
| 1.2.1 | 2026-11-06 | The GHCR mirror's failure explained correctly: the package exists and is linked to crunchtools/josui, so the fix is a grant, not a creation. RT#1556 |
| 1.2.0 | 2026-11-06 | `sdlc` on PATH, resolving the rev the repo pins rather than any default, so a gate run by hand is the same tool CI runs. RT#1556 |
| 1.1.0 | 2026-11-06 | A seat's `sdlc push` prerequisites move into the image: a git identity from the environment, and tool API keys from a read-only mcp-env mount. Layer 5 now names two credentials rather than one. Measured: 293s for trentina's push tier in a seat against 416s on the laptop. RT#1556 |
| 1.0.0 | 2026-11-06 | Extracted from josui 0.8.0, where this image was `Containerfile.dev`. Carries the measurements that justify its deviations: the five-capability set, `label=disable` with no AVC to replace it, `unmask=/proc/*`, the unprivileged UID map, and the fact that liveness is not monitorable from inside. RT#1556 |
