# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- The GHCR mirror push is `continue-on-error`. `ghcr.io/crunchtools/developer`
  does not exist and `GITHUB_TOKEN` cannot create an org package, so that push
  fails with `denied: permission_denied: write_package` on every merge. Quay is
  what production pulls and it succeeds; a default branch left red over a mirror
  only teaches the fleet's red-branch alert to be ignored. The guard comes off
  when the package exists. RT#1556

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
