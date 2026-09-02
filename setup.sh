#!/usr/bin/env bash
# Installs or updates mag on this server from a checkout, including the
# mag-proxy nginx site, the mag-proxy systemd unit, and a global `mag` CLI
# wrapper. Safe to re-run.
#
#   git pull
#   sudo ./setup.sh
#
# This directory must be a real `git clone`, not just a copy of the source.
# git is what we use for upgrades, versioning and rollbacks, and this script
# itself relies on git commands.
#
# Per-deployment config (MYOB credentials, this server's domain) lives in
# .env at the repo root - gitignored, never committed. If it's missing or
# incomplete, this script prompts for what's missing and adds it, so there's
# exactly one place to configure a fresh install.
#
# What this does NOT do:
#   - obtain MYOB API credentials (but this script will ask for them)
#   - run the one-time OAuth consent flow (mag oauth, once installed below)
#   - provision the TLS cert (certbot) or DNS - see README.md Setup
#
# See README.md#setup for the full walkthrough.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./setup.sh" >&2
    exit 1
fi

# Resolve to wherever this checkout actually lives, so the install isn't
# tied to any particular path (e.g. /opt/mag) - `git clone` it anywhere,
# then run setup.sh from there.
MAG_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAG_USER=mag
MAG_GROUP=mag
ENV_FILE="$MAG_HOME/.env"

echo "==> mag setup running from $MAG_HOME"

# --- 0. preflight checks --------------------------------------------------
# Fail clearly up front, if we can.
missing=()
for cmd in git python3 systemctl; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "ERROR: missing required command(s): ${missing[*]}" >&2
    echo "       mag needs git (the deploy method), python3 (runs mag itself), and" >&2
    echo "       systemd/systemctl (supervises mag-proxy) - see README.md#setup." >&2
    exit 1
fi

# git reports "dubious ownership" for any user other than whoever pulled
# the repo. We run git as root here, and later $MAG_HOME will be owned by
# $MAG_USER. So tell git it's okay for group members to run git commands.
git config --system --add safe.directory "$MAG_HOME"
# So `git pull` as a group member works too.
git -C "$MAG_HOME" config core.sharedRepository group

# Refuse to deploy an uncommitted working tree. The rollback mechanism
# (see README.md) only works if whatever's actually running always
# corresponds to a real commit. Includes untracked files (--porcelain).
if [[ -n "$(git -C "$MAG_HOME" status --porcelain)" ]]; then
    echo "REFUSING to deploy: $MAG_HOME has uncommitted changes." >&2
    echo "Commit or stash them first - see README.md#upgrade for why." >&2
    exit 1
fi

# --- 1. system user -----------------------------------------------------
# The systemd unit runs as this user; it also needs to own the checkout
# (tokens.json / mag_tokens.json / proxy_audit.log live under var/; .env
# lives at the repo root).
if ! id -u "$MAG_USER" &>/dev/null; then
    echo "==> creating system user $MAG_USER"
    useradd --system --home "$MAG_HOME" --shell /usr/sbin/nologin "$MAG_USER"
fi

# Create var/ before the chown/chmod sweep below, so it's covered by the
# same pass rather than needing its own - see mag/lib/paths.py.
mkdir -p "$MAG_HOME/var"

# Group-share the checkout with whoever ran this script, rather than
# needing `sudo -u mag` before every mag/git command (setuid doesn't work
# here - Linux ignores it on scripts entirely). Safe specifically because
# that operator already has root, same as running this script did - group
# access grants nothing sudo doesn't already give them. Same pattern
# Ghost-CLI uses (775/664, group-shared) rather than a dedicated-user
# requirement for CLI operations.
chown -R "$MAG_USER:$MAG_GROUP" "$MAG_HOME"
chmod -R g+rwX "$MAG_HOME"
# Both the root and var/ specifically get g+s. Applies to things created
# within *after* the bit is set, so var/ needs its own.
chmod g+s "$MAG_HOME" "$MAG_HOME/var"

if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
    # Add user to mag group to save doing `sudo -u mag` all the time.
    candidate_groups=("$MAG_GROUP")
    # Also allow `journalctl -u mag-proxy` (and `mag status`) without sudo.
    # Skipped if the group doesn't exist (some minimal systemd installs).
    if getent group systemd-journal &>/dev/null; then
        candidate_groups+=("systemd-journal")
    fi
    # Checked existing groups to avoid missing some because "already set up".
    missing_groups=()
    for g in "${candidate_groups[@]}"; do
        id -nG "$SUDO_USER" | grep -qw "$g" || missing_groups+=("$g")
    done
    # Finally, apply the missing groups.
    if [[ ${#missing_groups[@]} -gt 0 ]]; then
        echo "==> adding $SUDO_USER to: ${missing_groups[*]}"
        usermod -aG "$(IFS=,; echo "${missing_groups[*]}")" "$SUDO_USER"
        echo "    (log out and back in, or run 'newgrp $MAG_GROUP', for this to take effect)"
    fi
fi

# --- 2. per-deployment configuration (.env) ------------------------------
# Belt-and-braces: refuse to write secrets anywhere git could ever track,
# even if this script or .gitignore itself has a bug.
if ! git -C "$MAG_HOME" check-ignore -q .env; then
    echo "REFUSING to continue: .env is not gitignored in this checkout." >&2
    echo "Add '.env' to .gitignore before running setup.sh." >&2
    exit 1
fi

touch "$ENV_FILE"
chmod 660 "$ENV_FILE"
chown "$MAG_USER:$MAG_GROUP" "$ENV_FILE"
# shellcheck disable=SC1090
source "$ENV_FILE"

prompt_if_unset() {
    local var_name="$1" prompt_text="$2" is_secret="${3:-}"
    if [[ -n "${!var_name:-}" ]]; then
        return
    fi
    if [[ ! -t 0 ]]; then
        echo "$var_name is not set in $ENV_FILE, and this isn't an interactive terminal to prompt on." >&2
        echo "Set it in $ENV_FILE (see .env.example) and re-run." >&2
        exit 1
    fi
    local value
    if [[ "$is_secret" == "secret" ]]; then
        read -r -s -p "$prompt_text: " value
        echo
    else
        read -r -p "$prompt_text: " value
    fi
    printf '%s=%s\n' "$var_name" "$value" >> "$ENV_FILE"
    export "${var_name?}=$value"
}

echo "==> checking .env"
prompt_if_unset MYOB_CLIENT_ID     "MYOB_CLIENT_ID"
prompt_if_unset MYOB_CLIENT_SECRET "MYOB_CLIENT_SECRET" secret
prompt_if_unset MAG_DOMAIN         "Domain this server is reachable at (e.g. mag.example.com)"

# --- 3. nginx site ---------------------------------------------------------
if [[ ! -d /etc/nginx ]]; then
    echo "==> nginx not found (/etc/nginx missing) - skipping nginx site install." >&2
    echo "    mag needs a reverse proxy in front of it for /callback and /proxy/ -" >&2
    echo "    configure your own web server manually using templates/mag-proxy.conf as a template." >&2
elif ! systemctl is-active --quiet nginx; then
    echo "==> nginx is installed but not running - skipping nginx site install." >&2
    echo "    start it (systemctl start nginx) and re-run, or configure your own" >&2
    echo "    web server manually using templates/mag-proxy.conf as a template." >&2
elif [[ ! -f "/etc/letsencrypt/live/$MAG_DOMAIN/fullchain.pem" || ! -f "/etc/letsencrypt/live/$MAG_DOMAIN/privkey.pem" ]]; then
    # templates/mag-proxy.conf hardcodes this same path - without this check,
    # the failure a missing cert actually causes is nginx -t rejecting the
    # config over a certificate path that doesn't exist, several steps
    # further into this script and less clearly tied to the actual cause.
    echo "==> no TLS cert found for $MAG_DOMAIN - skipping nginx site install." >&2
    echo "    get one first (see README.md, Setup step 2):" >&2
    echo "        sudo certbot certonly --nginx -d $MAG_DOMAIN" >&2
    echo "    then re-run this script." >&2
else
    echo "==> installing nginx site for $MAG_DOMAIN"
    sed "s|__MAG_DOMAIN__|$MAG_DOMAIN|g" "$MAG_HOME/templates/mag-proxy.conf" > /etc/nginx/sites-available/mag-proxy.conf
    ln -sf /etc/nginx/sites-available/mag-proxy.conf /etc/nginx/sites-enabled/mag-proxy.conf
    nginx -t
    systemctl reload nginx
fi

# --- 4. mag-proxy systemd unit -------------------------------------------
# Rendered from the templates/ template with this checkout's real path
# substituted in.
echo "==> installing mag-proxy systemd unit"
sed "s|__MAG_HOME__|$MAG_HOME|g" "$MAG_HOME/templates/mag-proxy.service" > /etc/systemd/system/mag-proxy.service
systemctl daemon-reload
systemctl enable mag-proxy.service
systemctl restart mag-proxy.service   # restart (not start) so a redeploy picks up new code too

# `restart` above returning success only means the process was launched, not
# that it's still running - Type=simple doesn't wait around to confirm that.
# Check for a crash (eg. import error, port already in use) after a short pause.
sleep 2
if ! systemctl is-active --quiet mag-proxy.service; then
    echo "ERROR: mag-proxy.service isn't running right after being (re)started." >&2
    echo "       Check what's wrong. These commands may help:" >&2
    echo "           systemctl status mag-proxy.service" >&2
    echo "           journalctl -u mag-proxy.service -n 50 --no-pager" >&2
    exit 1
fi

# --- 5. global `mag` CLI ----------------------------------------------
# A thin wrapper, since we don't need the complexity of a setuptools project.
# Puts `mag` on PATH, loads the same .env as mag-proxy.service does, and
# puts the project on PYTHONPATH so `import mag...` resolves without an
# install step. Rendered from the templates/ template, same as the nginx site
# and systemd unit above.
echo "==> installing /usr/local/bin/mag"
sed "s|__MAG_HOME__|$MAG_HOME|g" "$MAG_HOME/templates/mag" > /usr/local/bin/mag
chmod 755 /usr/local/bin/mag

echo "==> done."
if [[ ! -f "$MAG_HOME/var/tokens.json" ]]; then
    echo "NEXT STEP: run 'newgrp $MAG_GROUP' to pick up the new group"
    echo "           membership, then run 'mag oauth' to authorize with"
    echo "           MYOB. Finally, run:"
    echo "               sudo systemctl restart mag-proxy.service"
fi
