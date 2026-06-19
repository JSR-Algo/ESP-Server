import html
import json

from aiohttp import web


class LessonAssignmentConsoleHandler:
    def __init__(self, config: dict, lesson_connections=None):
        self.config = config
        self.lesson_connections = lesson_connections if lesson_connections is not None else {}

    def _backend_api_url(self) -> str:
        server_config = self.config.get("server", {}) if isinstance(self.config, dict) else {}
        api_url = server_config.get("api_url") or self.config.get("api_url")
        if isinstance(api_url, str) and api_url.strip():
            return api_url.rstrip("/")
        return "https://tbot-backend-8wmh.onrender.com/v1"

    def _connected_device_ids(self) -> list[str]:
        return sorted(str(device_id) for device_id in self.lesson_connections.keys())

    async def handle_get(self, _request):
        api_url = html.escape(self._backend_api_url(), quote=True)
        devices_json = json.dumps(self._connected_device_ids())
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
        <input id="deviceId" list="connectedDevices" autocomplete="off" placeholder="device uuid or current robot id">
        <datalist id="connectedDevices"></datalist>
      </label>
    </div>
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
connectedDevices.forEach((deviceId) => {{
  const option = document.createElement('option');
  option.value = deviceId;
  document.getElementById('connectedDevices').appendChild(option);
}});
if (connectedDevices.length === 1) document.getElementById('deviceId').value = connectedDevices[0];

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
