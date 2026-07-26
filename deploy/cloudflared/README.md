# Cloudflare Tunnel ingress

`config.yml.example` is the source-controlled ingress contract for the
production tunnel. It contains placeholders only; do not commit the rendered
configuration or any tunnel credential file.

Cloudflare Tunnel evaluates ingress rules from top to bottom. The exact public
lesson generation reads therefore go to host Nginx before the admin and ESP
catch-alls. Nginx then applies the public-read policy and sends the latest index
to CMS or the generation status to ESP. Existing OTA, internal HTTP, MCP vision,
WebSocket, ESP catch-all, and terminal 404 behavior remains unchanged.

## Prepare a candidate on the VPS

Copy the template to a root-only candidate outside the release directory, then
replace `<TUNNEL_UUID>` and `<CREDENTIALS_FILE>` with the values already present
in the active VPS configuration. Do not paste those values into source control
or command output captured as deployment evidence.

```sh
sudo install -m 600 deploy/cloudflared/config.yml.example /etc/cloudflared/config.yml.candidate
sudo editor /etc/cloudflared/config.yml.candidate
```

Validate the candidate before replacing the active file:

```sh
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress validate
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress rule https://admin.tjbot.vn/v1/public/lesson-assets/latest
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress rule https://admin.tjbot.vn/public/lesson-assets/generation
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress rule https://esp.tjbot.vn/v1/public/lesson-assets/latest
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress rule https://esp.tjbot.vn/public/lesson-assets/generation
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress rule https://esp.tjbot.vn/tbot/ota/
sudo cloudflared --config /etc/cloudflared/config.yml.candidate tunnel ingress rule https://esp.tjbot.vn/tbot/v1/
```

Expected services are:

| URL | Matching service |
| --- | --- |
| Admin latest index | `http://127.0.0.1` |
| Admin generation status | `http://127.0.0.1` |
| ESP latest index | `http://127.0.0.1` |
| ESP generation status | `http://127.0.0.1:8003` (ESP catch-all) |
| ESP OTA | `http://127.0.0.1:8003` |
| ESP WebSocket | `http://127.0.0.1:8000` |

## Apply and verify

Back up the active configuration, atomically install the validated candidate,
restart the tunnel, and confirm it remains active:

```sh
sudo cp -p /etc/cloudflared/config.yml /etc/cloudflared/config.yml.rollback
sudo install -m 600 /etc/cloudflared/config.yml.candidate /etc/cloudflared/config.yml
sudo systemctl restart cloudflared
sudo systemctl is-active cloudflared
sudo cloudflared --config /etc/cloudflared/config.yml tunnel ingress validate
```

Then run the public generation verifier and existing deployment smoke checks.
If validation, restart, or smoke fails, restore `config.yml.rollback`, restart
`cloudflared`, and repeat the active/service checks before investigating.

The repository contract test is:

```sh
python3 -m pytest main/tbot-server/tests/test_cloudflared_ingress_contract.py -q
```
