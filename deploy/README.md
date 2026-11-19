# Host side of the skills sync (#42)

The seats keep their josui-skills checkouts current themselves
(`claude-skills-sync`, every `JOSUI_SKILLS_SYNC_INTERVAL` seconds). What a
host adds is the monitoring route: Nagios runs only on gloval, so each host
copies its seats' status lines to pCloud, where `check_skills_sync.sh`
(crunchtools/nagios-agent) reads them.

| Host | Checkouts | Marker(s) |
|------|-----------|-----------|
| Dolza | one, shared: Josui Prime mounts it read-write and syncs it, the seats mount it read-only and only relink | `dolza.shared` |
| Exedore | one per seat, `/srv/maxN/data/josui-skills`, synced inside each seat | `exedore.max1` … `exedore.max4` |

Order: the seats first (an image with `claude-skills-sync`), then these
timers, then nagios-agent's `skills-sync.conf` and services on gloval. The
services warn "no marker" until a timer has pushed, so installing them first
only means a known WARNING until then.

rclone with a `pcloud:` remote is already on both hosts.

Dolza: a user unit, as the user that runs Josui Prime and the seats.

```bash
install -Dm0755 deploy/skills-sync-push ~/.local/bin/skills-sync-push
install -Dm0644 deploy/dolza/skills-sync-push.{service,timer} -t ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now skills-sync-push.timer
```

Exedore: system units. Its seats run under root's podman and root has no
linger, so a root user timer would never fire. Exedore is a bootc host, so
`/usr/local` is read-only; the script goes in `/var/usrlocal/bin`.

```bash
install -Dm0755 deploy/skills-sync-push /var/usrlocal/bin/skills-sync-push
install -Dm0644 deploy/exedore/skills-sync-push.{service,timer} -t /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now skills-sync-push.timer
```

The unit runs as root with `RCLONE_CONFIG=/etc/rclone.conf`, where Exedore
keeps the `pcloud:` remote (root has no rclone config of its own), and is
sandboxed: `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
`NoNewPrivileges`.

A seat writes its status 0644 from a container subuid, and every directory on
the way is 0755, so root (or anyone) can read it:
`head -1 /srv/max1/data/josui-skills/.git/skills-sync.status`.
