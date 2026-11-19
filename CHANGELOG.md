# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Deprecated
- For one release the old names stay: the image is also published as
  `quay.io/crunchtools/developer` (a second gated build of each push to
  `main`), and the old command and hook paths are links to the new ones.
  `40-developer-seat.json` is not kept, since two copies would run every seat
  hook twice. Both go once Dolza's and Exedore's units name `claude`.

### Fixed
- Gatehouse gates no longer "COULD NOT RUN" on seats whose gateway URL names
  the gateway over plain http (`http://trentina:8019`, the Vertex seats)
  (#89). Gatehouse accepts plain http only to a literal private address, so
  the entrypoint resolves the name once at start and writes the address into
  `gatehouse.env` when it is private. A name resolving to a public address, or
  to nothing, is left for Gatehouse to refuse; https and literal addresses are
  untouched.

### Changed
- Renamed for the software rather than one role (#41): the repository is
  `crunchtools/claude` and the image `quay.io/crunchtools/claude`. In the
  image, `developer-entrypoint` is `claude-entrypoint`,
  `developer-worker-claude` is `claude-worker`, `developer-skills-sync` is
  `claude-skills-sync`, `/usr/libexec/developer` is `/usr/libexec/claude-seat`
  and the seat hooks are `40-claude-seat.json`.
- The container release runs constitution v1.51.0's `container-release.yml`
  (was v1.46.0) and grants it `issues: write`, so a failed scheduled rebuild
  opens an issue instead of only warning. A `v*` promote now also refuses an
  image whose `version` label names a different release of this repo. The release PR
  must keep moving the Containerfile label along with the other version strings.

## [1.17.1] - 2026-11-18

### Fixed
- `podman build` in a seat no longer runs out of space on the 1 GB `/var/tmp`
  tmpfs: `/etc/containers/containers.conf.d/50-image-copy-tmp-dir.conf` sets
  `image_copy_tmp_dir = "storage"`, so podman and buildah stage under the
  nested engine's storage volume. The entrypoint creates that directory
  (`~/.local/share/containers/storage/tmp`), which buildah will not. The
  units' `/var/tmp` cap stays as it is.

## [1.17.0] - 2026-11-18

### Added
- The `/ashigaru` skill, from the Ashigaru RPM, linked into Claude Code's
  managed skills folder (`/etc/claude-code/.claude/skills/ashigaru`), so every
  seat and Josui Prime can run it. josui-skills' `/agents` is unchanged.
- `ASHIGARU_URL` defaults to `https://ashigaru.crunchtools.com`. The key still
  comes only from the unit; without it `ashigaru` says "no API key" and
  ashigaru-hook stays silent.

### Changed
- Ashigaru 3.0.0 to 3.3.0 (`ASHIGARU_VERSION`, `ASHIGARU_SHA256`).

## [1.16.0] - 2026-11-18

### Added
- `TRENTINA_MCP_TOKEN_FILE`: the entrypoint reads the gateway token from that
  file, stripping one trailing newline, and it wins over `TRENTINA_MCP_TOKEN`.
  A file that is missing, unreadable, empty, over 4096 bytes, holds a NUL or
  is more than one line stops the start with a message naming the variable,
  never the value (#77).

### Fixed
- Managed settings deny `EnterPlanMode`. A seat that entered plan mode left it
  through ExitPlanMode's approval dialog, which only a person at the pane can
  answer and a cross-session message cannot, so a dispatched seat waited there
  overnight (a314). The brief a seat is dispatched with is the approved plan.
- `developer-skills-sync` no longer reports `state=current` when this pass's
  fetch failed. A checkout whose fetches always fail (a 401 from GitHub) kept
  comparing HEAD with a stale `origin/main` and read current with `fetched=0`.
  It now writes `state=stale`, aged from `drift_since`, so `check_skills_sync.sh`
  warns after an hour and goes critical after six.

## [1.15.0] - 2026-11-18

### Added
- The `ashigaru` CLI, from the Ashigaru 3.x release RPM (`ASHIGARU_VERSION`,
  pinned to 3.0.0), checked against `ASHIGARU_SHA256` before it is installed.
- Managed hooks run `/usr/libexec/developer/ashigaru-hook` beside seat-state
  on SessionStart, UserPromptSubmit, Stop, StopFailure and the idle_prompt
  Notification, reporting the seat's turn and task to the Desk through
  `ashigaru hook`. It does nothing until the seat has `ASHIGARU_URL` and a key
  (`ASHIGARU_API_KEY_FILE` or `ASHIGARU_API_KEY`). seat-state stays until the
  cutover.

### Fixed
- `sdlc` runs a converted repo's own `.sdlc/bin/sdlc` instead of exiting 2
  for want of a pinned python-sdlc `rev:`.

## [1.14.1] - 2026-11-17

### Fixed
- seat-state's handlers take an exclusive flock on
  `~/.claude/.seat-state.lock` around their read-modify-write, so two hooks
  running at once cannot interleave. An `idle_prompt` leaves alone a turn
  that started less than a minute ago: the notice comes at least that long
  after the turn it reports on, so a younger turn is a prompt that landed
  meanwhile, and the seat no longer reads idle while busy. A `turn_started`
  that is missing, malformed or has no timezone ends the turn.

## [1.14.0] - 2026-11-17

### Fixed
- seat-state treats a background task's result (a prompt opening with
  `<task-notification>`) like a peer's message: the turn is active, and
  `state`, `owner` and `task` are left alone, instead of the seat turning
  busy/manual with the XML as its task (#61).
- A turn interrupted with Esc no longer reads `active` until the next turn
  ends. A managed `Notification` hook on `idle_prompt`, which Claude Code
  sends a minute after any turn ends, interrupted ones included, runs
  `seat-state stop`. A seat that is already idle keeps its `turn_ended`.

### Added
- seat-state records each turn (#46): every prompt, a peer's message
  included, writes `turn: active` and `turn_started`; managed `Stop` and
  `StopFailure` hooks write `turn: idle` and `turn_ended`. A start or resume
  is idle too, so a seat that died mid-turn does not read active forever.
  /agents can check the turn before it types `/clear` instead of scraping the
  pane for a spinner.

## [1.13.0] - 2026-11-17

### Added
- A josui-skills merge reaches a running seat (#42, #48). `developer-skills-sync`
  runs at start, as the inline block did, and then every
  `JOSUI_SKILLS_SYNC_INTERVAL` seconds (600) in the background: it moves the
  checkout to the default branch, fetches, and fast-forwards. It never merges
  or resets, so a diverged checkout is left as it is and logged every pass.
  Skill links are updated one at a time, so a new skill appears and a removed
  one goes without a restart, and a running session never sees an empty
  skills directory. A checkout the seat cannot write (Dolza's shared one,
  read-only everywhere but Josui Prime) is only relinked. The loop is not
  supervised, so a sync that dies never takes the seat down, and it is
  stopped at every exit.
- Each pass over a checkout the seat can write records one `SKILLSSYNC`
  line in `.git/skills-sync.status` (state, HEAD, origin, when the drift
  began, the last good fetch); on Dolza that is Josui Prime's.
  `deploy/` holds the host side: `skills-sync-push` and a 10-minute user
  timer for Dolza and for Exedore copy those lines to
  `pcloud:Monitoring/SkillsSync/<host>.<seat>`, where nagios-agent's
  `check_skills_sync.sh` alerts on drift older than an hour.

### Changed
- claude-agent 1.2.0 (was 1.1.0): it requires tmux >= 3.2, logs keys it
  drops, and carries the supervisor fixes from crunchtools/claude-agent
  #30-#33.

### Fixed
- The background skills sync no longer ends without a word when its sleep
  is killed from outside: the loop is not under `set -e`, so the next pass
  just runs early (#49, review of #48).
- A checkout that cannot be fast-forwarded logs one line a pass with git's
  own reason, not git's hint block every ten minutes.
- `deploy/`: Exedore's push timer is a system unit. Its seats run under
  root's podman and root has no linger, so a user timer would never have
  fired. It reads `/etc/rclone.conf`, where Exedore keeps the `pcloud:`
  remote, and runs sandboxed. The README gives the install order (#51).

## [1.12.0] - 2026-11-17

### Added
- Seat state for /agents (#44). A managed hook writes
  `~/.claude/seat-state.json`: a prompt a person types marks the seat busy
  and manual, with the session's first prompt as its task; a cross-session
  message, which reaches the same hook, changes nothing. `/clear` frees the
  seat and pins the new conversation's UUID, so `developer-worker-claude`
  does not resume the cleared one after a respawn.
- Managed `autoCompactWindow: 500000`, so seats compact well before their
  conversations get huge.
- An empty skills volume is cloned from `crunchtools/josui-skills` at start
  (#42); an existing checkout is fast-forwarded as before.

### Fixed
- `org.opencontainers.image.version` is this image's release, not the base
  image's (it read 5.8.9 on 1.11.0). The boot check fails the build when it
  and `version` disagree.

## [1.11.0] - 2026-11-16

### Changed
- A worker seat (`JOSUI_MODE=worker`) keeps one conversation, and so one
  claude.ai Remote Control entry, across restarts instead of adding a new
  one each time. The new `developer-worker-claude` pins a session UUID in
  `~/.config/josui/session-id`, resumes it (`--resume <uuid>`) when its
  transcript holds a user turn, and otherwise starts it (`--session-id
  <uuid>`). A transcript with no conversation in it (empty, corrupt, only
  metadata), on which Claude Code exits 1, gets a new UUID rather than a
  respawn loop. A conversation run by hand in the work dir is never taken
  over. The tmux pane runs the wrapper; the texted worker (Josui Prime) runs
  it through claude-agent's `CLAUDE_AGENT_CLAUDE`. It decides at every
  launch, so the pane-died hook and `CLAUDE_AGENT_RESPAWN` resume too.

### Fixed
- The entrypoint's usage comment names this image, not the retired
  `quay.io/crunchtools/josui` (crunchtools/josui#71).

## [1.10.0] - 2026-11-16

### Changed
- claude-agent 1.1.0 (was 1.0.1): it holds Claude Code in tmux itself, on a
  server of its own. A texted worker (Josui Prime) no longer gets a `dev`
  session running `claude-agent attach --rw` in a loop, which would now nest
  tmux; a person attaches with `podman exec -it <service> claude-agent attach
  --rw`, and the startup line says so. Worker seats without a homeserver are
  unchanged.

## [1.9.1] - 2026-11-16

### Fixed
- `PYTEST_XDIST_AUTO_NUM_WORKERS=4` in the image. A repo with `-n auto` in its
  pytest addopts (trentina) started 24 xdist workers on a 24-core host, ~280 MB
  each, past the seat's memory limit; the OOM killer took the Claude session
  with them, three times on Exedore in one night. An explicit `-n N` still wins.

## [1.9.0] - 2026-11-14

### Added
- ssh for Dolza admin seats (constitution 3.0.0): a unit that mounts a seat's
  key, ssh config and known_hosts at `JOSUI_SSH_DIR` gets them copied to tmpfs
  (`JOSUI_SSH_RUNTIME`, default `/tmp/josui-ssh`) with `~/.ssh` linked there,
  so no key is ever written to the `$HOME` volume. With nothing mounted,
  nothing happens.

## [1.8.2] - 2026-11-14

### Fixed
- `LANG=C.UTF-8` in the image. `podman exec -it <seat> tmux attach -t dev`
  had no locale, so tmux replaced every non-ASCII character with `_` and the
  Claude Code screen came out garbled.

## [1.8.1] - 2026-11-14

### Fixed
- claude-agent 1.0.1 (was 0.2.1): the Matrix bridge names itself, so a
  homeserver behind Cloudflare no longer refuses it with error 1010. Josui
  Prime's texting depends on it.

## [1.8.0] - 2026-11-14

### Added

- A worker with `JOSUI_MATRIX_HOMESERVER` (Josui Prime, texted over RCS) runs
  its one session under `claude-agent run`, so texts, Remote Control, voice
  and the terminal all reach the same conversation. tmux window 0 runs
  `claude-agent attach --rw`.

## [1.7.0] - 2026-11-14

### Added

- Josui Prime runs this image (worker mode with `JOSUI_RC=1`), replacing the
  separate UBI `josui` image for the dispatcher.

### Fixed

- A person's acceptance of the Bypass Permissions warning survives a restart.
  The mounted `settings.json` replaced it at every start, so each restart
  asked again and a respawned worker waited at the warning.

## [1.6.0] - 2026-11-12

### Fixed

- `:latest` follows the gated `:main` (`cascade: true`). With no tag releases it
  had frozen on the last image built before build-once (RT#1550).

### Changed

- Builds move to constitution v1.39.1's container-release workflow: build once, gate the pushed digest (the toolchain check, run with podman against the digest), promote by digest. This image does not tag-release: `:main` follows merges and the units pull `:main`. GHCR is dropped; Quay.io is the only registry. RT#1550

## [1.5.1] - 2026-11-08

### Fixed

- The image carries a `version` label (1.5.0). The release sweep moves the
  version in tracked files, and with none stating it outside the changelog it
  refused to cut this repository's next release.

## [1.5.0] - 2026-11-07

### Changed

- **Gatehouse gets its own LLM endpoint on the gateway**, not the one the L3
  defense judge uses. `GATEHOUSE_LLM_BASE_URL` now derives
  `/proxy/llm/openrouter-gatehouse/v1`. The gateway serves OpenRouter twice
  under two names, because `llm_keys` is *both* the judge's credential
  (resolved by `defense.provider`) and what the proxy injects on
  `/proxy/llm/<name>` — so one provider forced judging and Gatehouse onto one
  key and made per-function spend unreadable. Two names, two keys, one
  upstream, and all Gatehouse usage lands on the Gatehouse key wherever it
  runs: seats, breetai, CI and Ashigaru. RT#1559

### Fixed

- **A seat runs the skills repository's default branch, whatever its volume
  was seeded with.** The entrypoint pulled whichever branch the checkout was
  on. A volume left on a feature branch therefore pulled that branch's upstream
  on every start, which never moved, and reported success: developer1 ran
  skills three weeks and 91 commits behind `main`, and `/feature` still sent
  deploys to lotor. It now refreshes `origin/HEAD`, moves to that branch, and
  then pulls. A move that would overwrite local changes is refused and logged,
  and the checkout keeps working as it is.

## [1.4.0] - 2026-11-07

### Changed

- **Gatehouse reviews through the gateway, not straight to OpenRouter.**
  `developer-entrypoint` now writes `~/.config/mcp-env/gatehouse.env` itself,
  setting `GATEHOUSE_LLM_BASE_URL` to the gateway's
  `/proxy/llm/openrouter/v1` route and using the seat's own
  `TRENTINA_MCP_TOKEN` as the key. The URL's origin is derived from
  `TRENTINA_MCP_URL` and is not settable on its own: this file carries the
  gateway token, so anything able to set the URL independently could redirect
  that token. Gatehouse's own review of the change raised that, correctly.
  A `gatehouse.env` in the mount **wins**, and
  the entrypoint writes nothing when one is there, so deploying this image
  changes nothing by itself. Deleting the mounted key is the cutover, per
  seat; re-placing it is the rollback. A seat with no gateway token or no
  gateway URL also leaves the mount alone, because failing there would take
  away the only Gatehouse it can run.

### Deprecated

- **The mounted OpenRouter API key.** It is a second, spendable copy of a
  credential the gateway already holds, with no per-seat accounting and no
  revocation short of editing files on gloval. Its *route* was never outside
  the gateway — `openrouter.ai` is on the profile's egress allowlist and the
  CONNECT proxy carries the traffic — but the key is. This release makes the
  gateway route available; removing the key is an operator step, taken once
  every repo a seat pushes is pinned to python-sdlc v0.5.2 or later, and the
  `openrouter.ai` egress grant comes off with it. RT#1559

  **The floor is per pushed repo, not per image.** A seat's gate runs
  Gatehouse out of the python-sdlc image at the rev the *pushed repo* pins, so
  this image cannot raise that floor for anything but itself. At the time of
  writing `trentina`, `josui` and `acquacotta` pin v0.5.1, `rotv` v0.5.0 and
  `claude-agent` v0.3.6 — all baking Gatehouse below 0.24.0. Deleting the key
  before those move would turn their push gates into 401s.

- **The pinned `python-sdlc` rev moves to v0.5.2**, which bakes Gatehouse
  0.24.0. That is a requirement of the change above, not housekeeping: 0.23.0
  and earlier ignore `GATEHOUSE_LLM_BASE_URL` and would send the gateway token
  to openrouter.ai as an OpenRouter key — a 401 on every push gate on every
  seat. 0.24.0 is also where a plaintext base URL to a private address became
  acceptable, which this route needs because the gateway has no TLS listener
  on the container bridge (crunchtools/gatehouse#115). The rev and the
  entrypoint function move together.

### Verified

- The gateway's LLM route answers from both the seat and the nested gate
  container that actually runs Gatehouse (`podman run` inside the seat
  inherits the seat's proxy environment and empty `resolv.conf`, so it is
  fenced the same way). Gatehouse's
  `provider: {"zdr": true, "data_collection": "deny"}` is an allowed key on
  the gateway's OpenRouter shape and the body is forwarded verbatim, so the
  zero-retention guarantee survives the move — the gateway returned 200 rather
  than refusing it as an unknown parameter.

## [1.3.0] - 2026-11-06

### Fixed

- **The GHCR mirror pushes, and the `continue-on-error` guard is gone.** Scott
  added `crunchtools/developer` to the existing package's *Manage Actions
  access* on 2026-11-06; the next run pushed clean to both registries. The
  package had been created by `crunchtools/josui` at 57c8e37, when this image
  was that repo's `Containerfile.dev`, and was still linked there, so this
  repo's token was granted nothing on it. RT#1556

### Changed

- **`permissions.defaultMode` is not used, and is not shipped.** It was added to
  one seat on gloval to make Remote Control sessions start in
  `bypassPermissions`. Scott tested write and delete from his phone in all three
  seats on 2026-11-06 and all three worked unprompted, while only that one seat
  carried the setting — so `JOSUI_PERMISSION_MODE` in the unit environment,
  which reaches each spawned child as `--permission-mode bypassPermissions`, is
  what carries it, and the claude.ai app does not override it per session. The
  setting was removed from that seat rather than rolled into this image: two
  mechanisms for one behaviour is how a later change breaks something nobody
  can explain. RT#1556

## [1.2.1] - 2026-11-06

### Fixed

- **A wrong explanation, which is a defect in its own right.** The 1.2.0 note
  and the workflow comment both said the GHCR mirror failed because
  `ghcr.io/crunchtools/developer` did not exist and CI could not create it. The
  package does exist, is publicly readable, and was created by
  `crunchtools/josui` at 57c8e37 when this image lived there; it is still linked
  to that repository, so this repo's `GITHUB_TOKEN` is granted nothing on it.

  Left standing, that comment sends the next person to create a package that is
  already there, watch the push fail again, and conclude the registry is broken.
  The real fix is one grant on the existing package — its settings, Manage
  Actions access, add `crunchtools/developer` with Write — after which the
  `continue-on-error` guard comes off. RT#1556

## [1.2.0] - 2026-11-06

### Added

- **`sdlc` on `PATH`.** The pre-push hook always found it, because pre-commit
  manages its own cache; a person typing in a seat's terminal did not, and
  running a repository's own gates by hand is what this image is for. The
  wrapper reads the `rev:` the repo pins for python-sdlc, clones that exact tag
  once into `~/.cache/crunchtools-sdlc`, and execs its launcher.

  It never guesses a version. python-sdlc's contract is that the `rev:` line
  fixes the launcher, the image and every tool in it for this machine and for
  CI, which read the same line — so a wrapper that defaulted would hand back a
  verdict CI does not share. No repo, no config, no python-sdlc entry, or a rev
  that is not a plain tag all exit 2, which is python-sdlc's own "could not
  run" and never to be worked around.

  Closes the last item of crunchtools/developer#2.

RT#1556

### Changed

- The GHCR mirror push is `continue-on-error`. It fails with
  `denied: permission_denied: write_package` on every merge. Quay is what
  production pulls and it succeeds; a default branch left red over a mirror only
  teaches the fleet's red-branch alert to be ignored. RT#1556

  **Corrected in 1.2.1:** this entry first said the package did not exist and
  that `GITHUB_TOKEN` could not create one. Both are wrong.
  `ghcr.io/crunchtools/developer` exists and is publicly readable; it was
  created by [crunchtools/josui](https://github.com/crunchtools/josui) at
  57c8e37, when this image was that repo's `Containerfile.dev`, and is still
  linked to that repository. The token here is scoped to this repo, which the
  package grants nothing. The fix is a grant on the existing package, not a
  creation.

## [1.1.0] - 2026-11-06

### Added

- **What a seat needs before `sdlc push` can run**, in the image rather than
  applied by hand to one container. Nothing is shared between developer1,
  developer2 and developer3 — separate state, nested-image, audit, skills and
  identity mounts, no overlap — so a fix applied to one seat exists only there,
  which is how these came to be hand-applied in the first place.
  - A git identity from `JOSUI_GIT_NAME` and `JOSUI_GIT_EMAIL`, set only when
    nothing is configured already, plus `advice.detachedHead false` because a
    pinned tool is cloned at its tag.
  - `*.env` files from a read-only `/etc/josui/mcp-env` mount, installed to
    `~/.config/mcp-env` at 0600, the way the identity is. Gatehouse dies
    without `gatehouse.env`, and that file holds an API key, so it belongs in a
    mount: not in the image, and not in the environment where `envscrub` would
    find it.

  Measured on developer3 against a trentina clone: the tier went from two gates
  reporting `COULD NOT RUN` to **293s, against 416s on the laptop** the same
  night, with mypy, gourmand, semgrep, pip-audit, constitution, codeql and
  gatehouse all green.

### Documented

- The two seat needs the image cannot supply on its own, both measured: `NO_PROXY`
  must list loopback, because podman passes `HTTP(S)_PROXY` into every nested
  gate container by default and a test talking to its own local server is
  otherwise tunnelled to the gateway; and the constitution checkout must be
  seeded per inherited version, because `sdlc`'s validator runs with `HOME=/tmp`
  and its plain clone never sees the gateway's `insteadOf` rewrite.

RT#1556

## [1.0.0] - 2026-11-06

### Added

- Extracted from [crunchtools/josui](https://github.com/crunchtools/josui) 0.8.0,
  where this image was `Containerfile.dev`. It publishes the same names —
  `quay.io/crunchtools/developer` and `ghcr.io/crunchtools/developer` — because
  the name was already right; what moved is where it is built. It lives here
  because the two images are expected to diverge, and a repo called josui is
  the wrong home for an image that is deliberately not one.
- The entrypoint is `developer-entrypoint`, and the tests run against it. The
  `JOSUI_*` environment prefix and the `/etc/josui`, `/var/lib/josui`,
  `/var/log/josui` mount paths are inherited unchanged: the three units on
  gloval were written against that contract, and renaming it is its own change
  with a window where both prefixes are accepted, not a flag day bundled into
  a move.
- Its own constitution (1.0.0), carrying the measurements that justify what
  this image is allowed to do: the five-capability set for nested podman,
  `label=disable` with the finding that no AVC exists to write a policy module
  from, `unmask=/proc/*`, the unprivileged UID map, and the fact that Remote
  Control liveness is not monitorable from inside.

RT#1556
