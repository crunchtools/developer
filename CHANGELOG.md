# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
