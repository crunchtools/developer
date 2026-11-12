# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
