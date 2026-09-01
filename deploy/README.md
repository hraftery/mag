# Deploying mag

[`setup.sh`](../setup.sh) (repo root) installs/updates everything needed
to run `mag/proxy/proxy.py` as a real service: the nginx site, the
`mag-proxy` systemd unit, and a global `mag` CLI wrapper. This directory
holds the templates it renders (`mag-proxy.conf`, `mag-proxy.service`).

See the main [README](../README.md#setup) for everything before this -
MYOB credentials, DNS/TLS for the OAuth redirect. This picks up assuming
that's already done and you're ready to install the service.

## Why systemd

`proxy.py` needs restart-on-crash, start-on-boot, and centralized
logs. systemd is already on the box (no new dependency, matching this
repo's stdlib-only ethos) and is exactly built for supervising a network
daemon like this. `mag oauth` (the OAuth authorization step) stays a
plain CLI command run by hand — it's one-shot and needs a human to click
through MYOB's consent screen, so there's no supervision problem for
systemd to solve there.

## Configuration

Everything specific to *this* deployment — MYOB credentials, the domain
this server answers on — lives in one file: `.env` at the repo root.
Gitignored, never committed. `setup.sh` prompts for whatever's missing
from it (so a fresh install needs no hand-editing), and both the
`mag-proxy` systemd unit and the `mag` CLI wrapper load it, so it's the
single place to configure a fresh install rather than values split
between `/etc`, the repo, and ad hoc exports. See
[`.env.example`](../.env.example) for the full list of keys.

## Why no `sudo -u mag`

`tokens.json` / `api_tokens.json` / `.env` need to be readable and
writable both by the `mag-proxy` systemd unit (runs as the `mag` user)
and by you, running `mag` commands by hand — otherwise every manual
command needs `sudo -u mag` first to keep file ownership from drifting.
Setuid doesn't solve this (Linux ignores the setuid bit on scripts
entirely - it only works on compiled binaries), so instead `setup.sh`
group-shares the checkout: files are `660` rather than `600`, and whoever
ran `setup.sh` (via `sudo`) is added to the `mag` group. Safe specifically
because that's already someone with root on this box - group access
grants nothing `sudo` doesn't already give them. Same pattern Ghost-CLI
uses (directories `775`, files `664`, group-shared) rather than requiring
a dedicated-user switch for every CLI operation.

**New group membership only applies to new sessions** - after the first
`sudo ./setup.sh`, either start a fresh login shell or run `newgrp mag` in
your current one before the plain `mag`/`git` commands below will work
without `sudo -u mag`.

## Install

On the server, as root:

```bash
git clone <this-repo-url> /opt/mag   # or wherever you'd like it - setup.sh doesn't assume a path
cd /opt/mag
sudo ./setup.sh
```

This creates a dedicated `mag` system user, adds you to its group (see
above), prompts for anything missing from `.env` (MYOB credentials, this
server's domain), installs the nginx site and `mag-proxy` systemd unit,
and puts a `mag` wrapper on `PATH`.

**Start a new session (or `newgrp mag`) so the group membership above
takes effect, then authorize with MYOB and restart the proxy:**

```bash
mag oauth                              # prints the MYOB consent URL - open it locally, approve
sudo systemctl restart mag-proxy.service
```

Confirm it worked: `test -f /opt/mag/var/tokens.json` and
`sudo systemctl status mag-proxy.service`.

**Verify end to end** — issue yourself a token locally (`mag issue ...`,
see the main README's [Usage](../README.md#usage)) and call the running
proxy (substitute your own `MAG_DOMAIN`):

```bash
curl -H "Authorization: Bearer <issued token>" https://mag.example.com/proxy/Sale/Invoice
tail -f /opt/mag/var/proxy_audit.log
journalctl -u mag-proxy.service -f
```

## Day to day

- **Status:** `mag status` - service state, recent logs, MYOB
  authorization, issued API tokens, and recent proxy activity, all in one
  place. Good first command when checking on a deployment.
- **Redeploy after a code change:**
  ```bash
  cd /opt/mag && git pull
  sudo ./setup.sh
  ```
  `setup.sh` is idempotent and restarts `mag-proxy.service` every run, so
  this is the entire update flow — no separate "just restart the service"
  step needed. It won't re-prompt for `.env` values that are already set.
- **Re-authorize** (refresh token expired or was revoked on MYOB's side):
  ```bash
  mag oauth
  sudo systemctl restart mag-proxy.service
  ```
- **Logs:** `mag status` includes both, or read them directly -
  `journalctl -u mag-proxy.service` (service lifecycle, stdout) vs.
  `/opt/mag/var/proxy_audit.log` (every proxied request: token name,
  method, path, status).
- **Tokens:** manage client API tokens on the server with
  `mag issue|list|edit|revoke` — see the main README's
  [Usage](../README.md#usage).

## Not yet done

Auto-deploy via GitHub Actions (SSH in, `git pull`, `setup.sh`) is a
reasonable next step but isn't set up here - it means an SSH deploy key
with production access living in GitHub secrets, which is worth a
deliberate decision (branch trigger, key scoping) rather than bundling in
by default. If you do want it later, it's a thin wrapper around exactly
the two commands in "Redeploy after a code change" above - `setup.sh` was
written to be that reusable core either way.
