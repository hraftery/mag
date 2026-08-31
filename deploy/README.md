# Deploying mag

systemd unit templates for `app/proxy_server.py` (the persistent proxy)
and `cli/oauth.py` (the one-shot OAuth authorizer), plus the runbook to
install them. See the main [README](../README.md#setup) for everything
before this - MYOB credentials, the OAuth redirect nginx config - this
picks up assuming that's already done and you're ready to run the
services under systemd instead of a manual foreground `python3` process.

## Why systemd

`proxy_server.py` needs restart-on-crash, start-on-boot, and centralized
logs - systemd is already on the box (no new dependency, matching this
repo's stdlib-only ethos) and is exactly built for supervising a network
daemon like this. `oauth.py` doesn't have a supervision problem to solve
(it's one-shot and needs a human to click through MYOB's consent screen),
so its unit is intentionally **not** enabled or started on boot - it just
gives it the same secret-injection and sandboxing as the proxy, invoked by
hand only when needed.

## One-time install

Run these on the server, as root (or with `sudo`).

**1. Create a dedicated system user and place the checkout:**

```bash
useradd --system --home /opt/mag --shell /usr/sbin/nologin mag
git clone <this-repo-url> /opt/mag
chown -R mag:mag /opt/mag
```

**2. Create the secrets file** (never committed - holds live MYOB
credentials):

```bash
install -d -m 700 -o mag -g mag /etc/mag
cp /opt/mag/deploy/mag.env.example /etc/mag/env
chmod 600 /etc/mag/env
chown mag:mag /etc/mag/env
# now edit /etc/mag/env and fill in MYOB_CLIENT_ID / MYOB_CLIENT_SECRET
```

**3. Install the units:**

```bash
cp /opt/mag/deploy/mag-proxy.service /opt/mag/deploy/mag-oauth.service /etc/systemd/system/
systemctl daemon-reload
```

**4. Seed `tokens.json`** (one-time MYOB authorization - see the main
README's [Setup step 3](../README.md#3-authorize-mag-with-myob) for what
this actually does):

```bash
systemctl start mag-oauth.service
journalctl -u mag-oauth.service -f   # prints the MYOB consent URL - open it locally, approve
```

Confirm it worked: `test -f /opt/mag/tokens.json`.

**5. Enable and start the proxy:**

```bash
systemctl enable --now mag-proxy.service
systemctl status mag-proxy.service
```

**6. Verify end to end** — issue yourself a token locally
(`python3 cli/mag.py issue ...`, see the main README's
[Usage](../README.md#usage)) and call the running proxy:

```bash
curl -H "Authorization: Bearer <issued token>" https://mag.example.com/proxy/Sale/Invoice
tail -f /opt/mag/proxy_audit.log
journalctl -u mag-proxy.service -f
```

## Day to day

- **Redeploy after a code change:**
  ```bash
  cd /opt/mag && sudo -u mag git pull
  systemctl restart mag-proxy.service
  ```
- **Re-authorize** (refresh token expired or was revoked on MYOB's side):
  ```bash
  systemctl start mag-oauth.service
  journalctl -u mag-oauth.service -f
  ```
- **Logs:** `journalctl -u mag-proxy.service` (service lifecycle, stdout)
  vs. `/opt/mag/proxy_audit.log` (every proxied request: token name,
  method, path, status).
- **Tokens:** manage client API tokens locally on the server with
  `python3 cli/mag.py issue|list|edit|revoke` (as the `mag` user, or as
  root then `chown mag:mag api_tokens.json`) - see the main README's
  [Usage](../README.md#usage).

## Not yet done

Auto-deploy via GitHub Actions (SSH in, `git pull`, restart the service)
is a reasonable next step but isn't set up here - it means an SSH deploy
key with production access living in GitHub secrets, which is worth a
deliberate decision (branch trigger, key scoping) rather than bundling in
by default.
