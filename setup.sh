#!/usr/bin/env bash
# Installs/updates mag on this server: the mag-proxy nginx site, the
# mag-proxy systemd unit, and a global `mag` CLI wrapper. Safe to re-run -
# that's the whole deploy/update flow:
#
#   git pull
#   sudo ./setup.sh
#
# Per-deployment config (MYOB credentials, this server's domain) lives in
# .env at the repo root - gitignored, never committed. If it's missing or
# incomplete, this script prompts for whatever's missing and writes it
# there, so there's exactly one place to configure a fresh install rather
# than values split between /etc, the repo, and hand-editing.
#
# What this does NOT do (can't be scripted - needs a human):
#   - obtain MYOB API credentials (but this script will ask for them)
#   - run the one-time OAuth consent flow (mag oauth, once installed below)
#   - provision the TLS cert (certbot) or DNS - see README.md Setup
#
# See README.md#setup and deploy/README.md for the full walkthrough.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./setup.sh" >&2
    exit 1
fi

# Resolve to wherever this checkout actually lives, so the install isn't
# tied to any particular path (e.g. /opt/mag) - "download latest source
# [anywhere], run setup.sh" should just work.
MAG_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAG_USER=mag
MAG_GROUP=mag
ENV_FILE="$MAG_HOME/.env"

echo "==> mag setup running from $MAG_HOME"

# --- 1. system user -----------------------------------------------------
# The systemd unit runs as this user; it also needs to own the checkout
# (tokens.json / api_tokens.json / proxy_audit.log / .env all live under it).
if ! id -u "$MAG_USER" &>/dev/null; then
    echo "==> creating system user $MAG_USER"
    useradd --system --home "$MAG_HOME" --shell /usr/sbin/nologin "$MAG_USER"
fi

# Group-share the checkout with whoever ran this script, rather than
# needing `sudo -u mag` before every mag/git command (setuid doesn't work
# here - Linux ignores it on scripts entirely). Safe specifically because
# that operator already has root, same as running this script did - group
# access grants nothing sudo doesn't already give them. Same pattern
# Ghost-CLI uses (775/664, group-shared) rather than a dedicated-user
# requirement for CLI operations.
chown -R "$MAG_USER:$MAG_GROUP" "$MAG_HOME"
chmod -R g+rwX "$MAG_HOME"
chmod g+s "$MAG_HOME"   # new files/dirs created here inherit the mag group
git -C "$MAG_HOME" config core.sharedRepository group   # so `git pull` as a group member works too

if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] && ! id -nG "$SUDO_USER" | grep -qw "$MAG_GROUP"; then
    echo "==> adding $SUDO_USER to the $MAG_GROUP group"
    usermod -aG "$MAG_GROUP" "$SUDO_USER"
    echo "    (log out and back in, or run 'newgrp $MAG_GROUP', for this to take effect)"
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
echo "==> installing nginx site for $MAG_DOMAIN"
sed "s|__MAG_DOMAIN__|$MAG_DOMAIN|g" "$MAG_HOME/deploy/mag-proxy.conf" > /etc/nginx/sites-available/mag-proxy.conf
ln -sf /etc/nginx/sites-available/mag-proxy.conf /etc/nginx/sites-enabled/mag-proxy.conf
nginx -t
systemctl reload nginx

# --- 4. mag-proxy systemd unit -------------------------------------------
# Rendered from the deploy/ template with this checkout's real path
# substituted in.
echo "==> installing mag-proxy systemd unit"
sed "s|__MAG_HOME__|$MAG_HOME|g" "$MAG_HOME/deploy/mag-proxy.service" > /etc/systemd/system/mag-proxy.service
systemctl daemon-reload
systemctl enable mag-proxy.service
systemctl restart mag-proxy.service   # restart (not start) so a redeploy picks up new code too

# --- 5. global `mag` CLI ----------------------------------------------
# A thin wrapper, not a real package install - cli/ and app/ stay the flat,
# dependency-free scripts they already are; this just puts `mag` on PATH
# and makes sure .env is loaded for manual invocations too (e.g. `mag
# oauth`), not just the systemd-supervised proxy.
echo "==> installing /usr/local/bin/mag"
cat > /usr/local/bin/mag <<EOF
#!/usr/bin/env bash
set -a
[ -f "$MAG_HOME/.env" ] && source "$MAG_HOME/.env"
set +a
exec python3 "$MAG_HOME/cli/mag.py" "\$@"
EOF
chmod 755 /usr/local/bin/mag

echo "==> done."
echo "    status:  systemctl status mag-proxy.service"
echo "    logs:    journalctl -u mag-proxy.service -f"
if [[ ! -f "$MAG_HOME/tokens.json" ]]; then
    echo "    NEXT STEP: in a NEW session (or after 'newgrp $MAG_GROUP' in this one -"
    echo "               group membership added above doesn't apply retroactively),"
    echo "               run 'mag oauth' to authorize with MYOB, then:"
    echo "               sudo systemctl restart mag-proxy.service"
fi
