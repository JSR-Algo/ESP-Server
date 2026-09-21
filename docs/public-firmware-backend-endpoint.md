# Public backend endpoint in OTA responses

Set `TBOT_PUBLIC_BACKEND_API_URL` when the robot reaches the Nest backend through
a different origin from the ESP runtime. M0 staging needs
`https://m0-api.tjbot.vn/v1`.

This override changes only the `api_url` advertised by OTA to firmware. Keep
`COURSE_BACKEND_URL=http://backend:3000/v1`, `server.api_url` and the lesson runtime
on their existing internal routes. Do not set `TBOT_BACKEND_API_URL` to compensate:
the configuration loader also uses that variable to select the runtime backend.

The public override requires HTTPS without credentials, query or fragment.
An invalid explicit value fails the OTA response instead of advertising an
internal fallback. When unset or blank, existing behavior is unchanged.
It does not alter device claiming, token minting, auth, firmware download
selection, or the WebSocket URL. Use the verified public WebSocket config
separately and retain all existing auth/claim gates.

The matching source/image and environment require an authorized staging deploy.
Verify the real OTA response, backend bootstrap/config agreement and negative
controls before enrolling the physical robot. Unit tests do not establish that
the robot has received or saved these endpoints.
