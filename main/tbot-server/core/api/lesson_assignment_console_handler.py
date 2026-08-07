import hmac
import html
import json
import os

from aiohttp import web

# Reused rather than reimplemented on purpose: a second production predicate that
# drifts from the first is the F-T64-03 failure mode (auth.guard vs auth.service
# disagreeing about what "production" meant let a committed key sign tokens).
from core.api.lesson_sd_fanout_handler import _production_environment

CONSOLE_ENABLED_ENV = "LESSON_ASSIGN_CONSOLE_ENABLED"


class LessonAssignmentConsoleHandler:
    def __init__(self, config: dict, lesson_connections=None):
        self.config = config
        self.lesson_connections = lesson_connections if lesson_connections is not None else {}

    @staticmethod
    def _console_served() -> bool:
        """Whether to serve the operator console at all.

        F-T64-05 / T6.4 deep-dive box 5 ("console requires auth — none anonymously
        callable"). The page cannot be gated by a header: an operator opens it in a
        browser to paste a parent JWT, and a browser cannot send X-Mint-Secret. It
        also cannot be gated by Nginx, because cloudflared routes the esp.tjbot.vn
        catch-all straight to :8003 and never traverses Nginx at all
        (deploy/cloudflared/config.yml.example).

        So in production it is not served unless someone deliberately turns it on.
        Outside production it is always available, which keeps local operator and
        e2e workflows unchanged.
        """
        if not _production_environment():
            return True
        return os.environ.get(CONSOLE_ENABLED_ENV, "").strip().lower() == "true"

    @staticmethod
    def _inventory_authorized(request) -> bool:
        """True when the caller proved the internal mint secret.

        The console shell itself stays reachable — an operator has to load the page
        in a browser before they can paste their parent JWT, and a browser cannot
        send X-Mint-Secret. The CONNECTED-ROBOT INVENTORY is a different matter: it
        pairs every live robot's MAC with its backend device UUID, and nginx proxies
        /tbot/ with no auth, so serving it unconditionally publishes the fleet to
        anyone who can reach the vhost.
        """
        expected = os.environ.get("TBOT_DEVICE_MINT_SECRET", "")
        if not expected:
            return False
        headers = getattr(request, "headers", None)
        provided = headers.get("X-Mint-Secret", "") if hasattr(headers, "get") else ""
        return bool(provided) and hmac.compare_digest(provided, expected)

    def _backend_api_url(self) -> str:
        config = self.config if isinstance(self.config, dict) else {}
        server_config = config.get("server", {})
        if not isinstance(server_config, dict):
            server_config = {}
        api_url = server_config.get("api_url") or config.get("api_url")
        if isinstance(api_url, str) and api_url.strip():
            return api_url.rstrip("/")
        return "https://tbot-backend-8wmh.onrender.com/v1"

    def _connected_devices(self) -> list[dict[str, str]]:
        """Connected robots as ``{mac, deviceId}``.

        The websocket registry is keyed by the robot MAC, but every backend
        assignment route is ``/devices/{uuid}/...`` behind a UUID param pipe — a MAC
        posted there can only ever 400. So each MAC is resolved through the already
        populated mint cache and offered as its backend device UUID; a MAC with no
        resolved UUID is reported as unresolved instead of being handed to the
        operator as if it were assignable.
        """
        try:
            from config.device_token_client import cached_device_uuid
        except Exception:  # pragma: no cover - import guard for trimmed builds
            def cached_device_uuid(_mac):
                return None

        devices = []
        for key in sorted(str(device_id) for device_id in self.lesson_connections.keys()):
            devices.append({"mac": key, "deviceId": cached_device_uuid(key) or ""})
        return devices

    @staticmethod
    def _script_safe_json(payload) -> str:
        """json.dumps for embedding inside a <script> block.

        json.dumps does NOT escape '<' or '/', so a device id containing
        ``</script>`` would close the block and inject markup. The websocket
        registry is keyed by the device-supplied ``device-id`` header, so those
        keys are untrusted input. Escaping the three HTML-significant characters
        as \\uXXXX keeps the value a valid JSON string while making it inert.
        """
        return (
            json.dumps(payload)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )

    async def handle_get(self, request):
        if not self._console_served():
            # 404, not 403: a 403 would confirm the route exists on every
            # production robot server to anyone who probes for it.
            return web.Response(status=404, text="Not Found", content_type="text/plain")

        api_url = html.escape(self._backend_api_url(), quote=True)
        devices_json = self._script_safe_json(
            self._connected_devices() if self._inventory_authorized(request) else []
        )
        return web.Response(
            text=self._html(api_url=api_url, devices_json=devices_json),
            content_type="text/html",
        )

    def _html(self, *, api_url: str, devices_json: str) -> str:
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TBOT Lesson Assignment</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f7f8fb; color: #171923; }}
    main {{ max-width: 920px; margin: 0 auto; padding: 24px; }}
    h1 {{ font-size: 24px; margin: 0 0 18px; }}
    section {{ background: #fff; border: 1px solid #d9dee8; border-radius: 8px; padding: 16px; margin: 14px 0; }}
    label {{ display: grid; gap: 6px; font-size: 13px; font-weight: 650; margin: 10px 0; }}
    input, select, textarea {{ box-sizing: border-box; width: 100%; border: 1px solid #c7cedb; border-radius: 6px; padding: 10px; font: inherit; }}
    textarea {{ min-height: 120px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    button {{ border: 0; border-radius: 6px; background: #2357c6; color: #fff; font-weight: 700; padding: 10px 14px; margin: 8px 8px 0 0; cursor: pointer; }}
    button.secondary {{ background: #475569; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }}
    .hint {{ color: #586174; font-size: 13px; line-height: 1.45; }}
    .error {{ color: #b42318; }}
    @media (max-width: 720px) {{ .grid {{ grid-template-columns: 1fr; }} main {{ padding: 16px; }} }}
  </style>
</head>
<body>
<main>
  <h1>TBOT Lesson Assignment</h1>
  <section>
    <div class="grid">
      <label>Backend API base
        <input id="apiBase" value="{api_url}" autocomplete="off">
      </label>
      <label>Parent JWT bearer token
        <input id="token" type="password" autocomplete="off" placeholder="Paste parent access token">
      </label>
      <label>Child ID
        <input id="childId" autocomplete="off" placeholder="child uuid">
      </label>
      <label>Device ID
        <input id="deviceId" list="connectedDevices" autocomplete="off" placeholder="backend device uuid">
        <datalist id="connectedDevices"></datalist>
      </label>
    </div>
    <p class="hint" id="deviceHint"></p>
    <p class="hint">Token stays in this page memory only. It is sent to backend APIs as Authorization and is not stored by the ESP server.</p>
  </section>
  <section>
    <button onclick="loadCourses()">Load courses</button>
    <div class="grid">
      <label>Course
        <select id="courseId" onchange="loadLessons()"></select>
      </label>
      <label>Lesson
        <select id="lessonId"></select>
      </label>
    </div>
    <button onclick="assignLesson()">Assign selected lesson</button>
    <button class="secondary" onclick="enrollCourse()">Assign whole course</button>
  </section>
  <section>
    <label>Result
      <textarea id="result" readonly></textarea>
    </label>
  </section>
</main>
<script>
const connectedDevices = {devices_json};
const assignableDevices = connectedDevices.filter((device) => device.deviceId);
const unresolvedDevices = connectedDevices.filter((device) => !device.deviceId);
assignableDevices.forEach((device) => {{
  const option = document.createElement('option');
  option.value = device.deviceId;
  option.label = device.mac;
  document.getElementById('connectedDevices').appendChild(option);
}});
// Only a resolved backend UUID may be prefilled: the assignment routes reject a
// MAC, so prefilling one would make every submit fail for a non-obvious reason.
if (assignableDevices.length === 1) document.getElementById('deviceId').value = assignableDevices[0].deviceId;
document.getElementById('deviceHint').textContent = unresolvedDevices.length
  ? `Connected robots without a resolved backend device UUID: ${{unresolvedDevices.map((device) => device.mac).join(', ')}}. Their MAC is not accepted by the assignment API — look the device UUID up in the backend.`
  : (assignableDevices.length ? '' : 'No connected robot has a resolved backend device UUID.');

function value(id) {{ return document.getElementById(id).value.trim(); }}
function setResult(payload, isError = false) {{
  const result = document.getElementById('result');
  result.className = isError ? 'error' : '';
  result.value = typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2);
}}
async function api(path, options = {{}}) {{
  const token = value('token');
  if (!token) throw new Error('Parent JWT is required');
  const response = await fetch(value('apiBase').replace(/\\/+$/, '') + path, {{
    ...options,
    headers: {{
      'Content-Type': 'application/json',
      Authorization: `Bearer ${{token}}`,
      ...(options.headers || {{}}),
    }},
  }});
  const body = await response.json().catch(() => ({{}}));
  if (!response.ok) throw new Error(JSON.stringify(body));
  return body.data || body;
}}
function unwrapList(payload, key) {{ return Array.isArray(payload) ? payload : (payload[key] || []); }}
async function loadCourses() {{
  try {{
    const data = await api('/courses');
    const courses = unwrapList(data, 'courses');
    const select = document.getElementById('courseId');
    select.innerHTML = '';
    courses.forEach((course) => {{
      const option = document.createElement('option');
      option.value = course.courseId || course.course_id || course.id;
      option.textContent = course.title || course.name || option.value;
      select.appendChild(option);
    }});
    setResult({{ courses }});
    await loadLessons();
  }} catch (err) {{ setResult(String(err.message || err), true); }}
}}
async function loadLessons() {{
  const courseId = value('courseId');
  if (!courseId) return;
  try {{
    const childId = value('childId');
    const query = childId ? `?childId=${{encodeURIComponent(childId)}}` : '';
    const data = await api(`/courses/${{courseId}}/lessons${{query}}`);
    const lessons = unwrapList(data, 'lessons');
    const select = document.getElementById('lessonId');
    select.innerHTML = '';
    lessons.forEach((lesson) => {{
      const option = document.createElement('option');
      option.value = lesson.lessonId || lesson.lesson_id || lesson.id;
      option.dataset.version = String(lesson.lessonVersion || lesson.lesson_version || 1);
      option.dataset.profile = lesson.profile || '';
      option.textContent = lesson.title || lesson.name || option.value;
      select.appendChild(option);
    }});
    setResult({{ lessons }});
  }} catch (err) {{ setResult(String(err.message || err), true); }}
}}
async function assignLesson() {{
  try {{
    const lessonSelect = document.getElementById('lessonId');
    const selected = lessonSelect.options[lessonSelect.selectedIndex];
    const deviceId = value('deviceId');
    const body = {{
      childId: value('childId'),
      lessonId: value('lessonId'),
      lessonVersion: Number(selected?.dataset.version || 1),
      profile: selected?.dataset.profile || 'espTft',
    }};
    const data = await api(`/devices/${{deviceId}}/assignments`, {{ method: 'POST', body: JSON.stringify(body) }});
    setResult(data);
  }} catch (err) {{ setResult(String(err.message || err), true); }}
}}
async function enrollCourse() {{
  try {{
    const courseId = value('courseId');
    const body = {{ childId: value('childId'), deviceId: value('deviceId') }};
    const data = await api(`/courses/${{courseId}}/enroll`, {{ method: 'POST', body: JSON.stringify(body) }});
    setResult(data);
  }} catch (err) {{ setResult(String(err.message || err), true); }}
}}
</script>
</body>
</html>"""
