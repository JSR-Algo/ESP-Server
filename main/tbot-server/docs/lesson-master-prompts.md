# Master Prompts: Load khoá học (esp server) → Robot → "Start Lesson" (Google Live API)

Tài liệu này gom các "master prompt" copy-paste được để chuỗi end-to-end chạy đúng: (1) robot trò chuyện bình thường bằng Google Gemini Live; (2) trẻ nói "học bài thôi" → model gọi tool `start_lesson`; (3) runtime tải khoá học đã giao từ esp server và dẫn trẻ qua các bước học được dựng sẵn.

Mọi khẳng định về code đều trích `path:line` so với mã thực tại thời điểm viết (2026-06-17).

---

## 0. Cải chính quan trọng so với giả định ban đầu (đọc trước khi dùng)

Bốn điểm dưới đây trong brief gốc **sai hoặc thiếu so với code thực** — mọi artifact đã chỉnh theo code:

1. **Live KHÔNG thiếu hoàn toàn `safety_settings` — nhưng chỉ đúng cho file `google_live/client.py`.** `core/voice/google_live/client.py:285` gán `connect_config["safety_settings"] = self._build_safety_settings()`, và `_build_safety_settings()` (`client.py:311–321`) đặt `BLOCK_LOW_AND_ABOVE` cho cả 4 nhóm (`HARASSMENT`, `HATE_SPEECH`, `SEXUALLY_EXPLICIT`, `DANGEROUS_CONTENT`). Cộng thêm hàng rào prompt-level `ensure_child_safety_block()` (`core/voice/child_safety.py:36`) và bộ lọc output `screen_model_output()` (`child_safety.py:44–63`). Phần "P0 gap: child Live có NO safety_settings" trong system-design **không còn đúng với riêng file này**; nó có thể vẫn đúng cho các provider/đường khác — **giữ phạm vi cải chính chỉ ở `google_live/client.py`**, đừng coi đây là phủ định toàn cục. Master prompt phần A vì vậy là lớp phòng vệ *bổ sung* (định nghĩa persona + quy tắc khó mã hoá thành threshold), không phải "vá lỗ thủng duy nhất".

2. **Lesson narration KHÔNG do model Live tự kể — nhưng nguồn TEXT của lời thoại KHÔNG có trong các file đã review.** Trong manifest thật (`docs/stories/US-006-learning-course-runtime/fixtures/lesson-protocol.v1.json:150` và `:302`), mỗi step có `body.audio = {"via": "tts"}` — **chỉ có khoá `via`, KHÔNG có trường text** (xác nhận tại fixture dòng 44, divergence #12: "body.audio keyset {via}"). Quan trọng hơn, `_meta.notes.audio` (fixture dòng 52) ghi rõ: *"Spoken prompt + child speech ride the existing tts/stt + binary audio path, **never inside lesson_step** (backward-compat)."* `core/lesson/runtime.py:_step_body` (`:655–673`) cũng chỉ forward `audio = step.get("audio")` nguyên trạng — không thêm text. Nghĩa là frame `lesson_step` chỉ chở **dữ liệu cảnh/visual** (`scene.*`); lời thoại tiếng Việt đi out-of-band qua đường tts/stt sẵn có, và **trường chứa TEXT của lời thoại đó KHÔNG nằm trong bất kỳ file nào ở `core/lesson/` hay trong fixture**. Vì vậy phần C bên dưới được **hạ cấp rõ ràng thành "đề xuất authoring guidance"**, không phải template "ánh xạ trực tiếp vào trường thật của step" như brief gốc nói.

3. **Tên bước (stepType) thật là 9 loại có tên, không phải chỉ 'passive'/'interactive'.** `core/lesson/runtime.py:633` liệt kê đúng: `greeting, review, focus, model, listen, repeat, fillBlank, feedback, celebrate`. Trường `completionClass` ('passive'|'interactive') là **classifier ưu tiên** (runtime.py:`_is_passive_step`, ~`:87–102`), nếu vắng thì fallback theo `PASSIVE_STEP_TYPES = {greeting, review, focus, feedback, celebrate}` (runtime.py:`:79–85`). Fixture freeze cả hai class end-to-end: step `model` (interactive, `timeoutSec:12`, fixture `:147–150`) **và** step `review` (passive, `timeoutSec:10`, fixture `:299–302`) — xác nhận multi-step playback (P5) + split passive/interactive.

4. **Cải chính lớn nhất — gate bật/tắt bài học là gate ADMISSION CỦA TOOL, không phải nhánh runtime nói-được.** Tool `start_lesson` **chỉ được gắn vào phiên Live khi `lesson.runtime_enabled == true`**: `core/providers/tools/product_toolset.py:31` `ALWAYS_INCLUDE_WHEN_LESSON_ENABLED = ("start_lesson",)` chỉ được `.extend` vào danh sách tool **bên trong** `if lesson_runtime_enabled(conn):` (`product_toolset.py:65–66`), và `_resolve_functions_for_live()` (`core/voice/session_provider/google_live.py:848–860`) dựng tool list từ `product_tool_names()` đó. **Hệ quả: khi `LESSON_RUNTIME_ENABLED` TẮT, model KHÔNG hề có tool `start_lesson` trong `connect_config.tools` → trẻ có nói "học bài thôi" thì cũng KHÔNG có tool nào để gọi.** Nhánh gate-OFF trong `start_lesson.py:75–79` (trả `"Lesson mode is not available right now."`) là **DEAD trên đường google_live** — nó chỉ đạt được qua đường KHÔNG-Live (vd intent-dispatch của classic pipeline), không qua Gemini Live. Brief gốc trình bày nhánh OFF như một nhánh runtime nói-được — đã sửa ở phần A và D.

---

## A. CONVERSATION-MODE MASTER SYSTEM PROMPT

**WHERE:** dán vào `config.yaml` → khối top-level `prompt: |` (`config.yaml:311`). Tại runtime, `GoogleLiveClient._build_system_instruction()` đọc `self.config.get("system_prompt") or self.config.get("prompt")` (`core/voice/google_live/client.py:324`), rồi bọc bằng `ensure_child_safety_block(prompt)` (`client.py:327`) → thành `system_instruction` của phiên Live (`client.py:302–304`).

**HAI điều phải biết về lớp an toàn (đã verify):**

- **Block an toàn force-prepend là TIẾNG ANH.** `ensure_child_safety_block` (`child_safety.py:36–41`) **strip mọi `<child_safety>…</child_safety>` cũ rồi prepend lại `CHILD_SAFETY_BLOCK`** — và block chuẩn đó **viết bằng tiếng Anh** (`child_safety.py:9–17`, nội dung: `"User: Vietnamese child, age band 4-8 … Hard refusals: … Anti-grooming: …"`). Đây là rủi ro robustness cho sản phẩm VI 4–8 tuổi: luật cấm cốt lõi tới model bằng ngôn ngữ KHÁC ngôn ngữ hội thoại. **Khuyến nghị team: localize `CHILD_SAFETY_BLOCK` sang tiếng Việt** (hoặc song ngữ) trong `child_safety.py`. Cho tới khi đó, **đoạn `[An toàn trẻ em]` tiếng Việt trong prompt dưới là lớp luật VẬN HÀNH chính** (belt-and-suspenders có chủ đích).

- **Đoạn an toàn VI trong prompt dưới sẽ KHÔNG bị de-dup.** Regex de-dup `_CHILD_SAFETY_RE` (`child_safety.py:25–28`) chỉ strip đúng literal cặp thẻ `<child_safety>…</child_safety>`. Đoạn `[An toàn trẻ em]` dưới đây **không có thẻ** → nó **đồng tồn tại** với block EN injected; không cái nào ghi đè cái nào — hai bộ luật chồng nhau theo thiết kế. Vì vậy **đừng tự nhúng `<child_safety>` vào prompt** (sẽ bị strip), nhưng **giữ đoạn VI dạng văn xuôi** thì an toàn và là điều ta muốn.

> Lưu ý shipped-config: `config.yaml:312–320` HIỆN đã nhúng sẵn một block `<child_safety>` — block đó **bị `ensure_child_safety_block` strip rồi thay bằng bản chuẩn EN** lúc runtime nên vô hại nhưng thừa. Khi cập nhật, nên gỡ block `<child_safety>` thừa trong `config.yaml` và chỉ giữ đoạn VI văn xuôi.

```text
Bạn là TBot — người bạn robot biết nói, ấm áp và vui vẻ, đồng hành cùng một em nhỏ 4–8 tuổi đang học tiếng Anh. Bạn nói qua loa của robot; em bé nghe bằng tai, không đọc chữ.

[Tính cách & giọng điệu]
- Nói tiếng Việt là chính, chèn từ/câu tiếng Anh NGẮN, đơn giản để bé luyện. Mỗi lượt chỉ 1–2 câu ngắn, không độc thoại dài.
- Giọng dịu dàng, kiên nhẫn, khích lệ. Khen cụ thể ("Con nói 'cat' rõ ghê!") thay vì khen chung chung.
- Khi bé im lặng hoặc bối rối, gợi ý nhẹ một lựa chọn, đừng dồn dập.
- Tự xưng "mình", gọi bé là "con". Không bao giờ giả vờ làm được việc ngoài khả năng (gọi điện, mở app, mua đồ...).

[An toàn trẻ em — luật cứng, tuân thủ tuyệt đối]
- Phạm vi cho phép: học tiếng Anh thân thiện, truyện/bài hát/từ vựng hợp lứa tuổi, chủ đề đời thường an toàn.
- TỪ CHỐI ngay (ngắn gọn, bằng tiếng Việt, KHÔNG giải thích chi tiết nội dung xấu) với: chuyện người lớn, bạo lực, tự làm hại bản thân, thù ghét, chửi thề, việc phạm pháp, hướng dẫn nguy hiểm.
- TUYỆT ĐỐI KHÔNG hỏi và KHÔNG nhận: tên thật đầy đủ, địa chỉ nhà, tên trường, số điện thoại, vị trí chính xác, mật khẩu, ảnh, chi tiết riêng tư của gia đình.
- Chống dụ dỗ (anti-grooming): không bao giờ rủ bé qua app khác, gặp ngoài đời, giấu cuộc trò chuyện, chia sẻ thông tin liên lạc, hay giữ bí mật với bố mẹ/người lớn tin cậy.
- Nếu có chuyện gì làm con buồn, sợ, hoặc không thoải mái, hãy nhẹ nhàng khuyên con kể với bố mẹ hoặc người lớn con tin tưởng.
- Khi cần từ chối: nói ngắn bằng tiếng Việt rồi chuyển hướng về một câu luyện tiếng Anh an toàn. Ví dụ: "Mình không nói về chuyện đó được. Mình cùng tập câu 'I like apples' nhé?"

[Khi nào GỌI tool start_lesson — và khi nào KHÔNG]
- CHỈ gọi start_lesson khi bé muốn VÀO ĐÚNG BÀI HỌC ĐÃ ĐƯỢC GIAO cho bé (bài học/tiết học/khoá học của con), KHÔNG phải khi bé chỉ muốn được dạy/chơi học ngay trong lúc trò chuyện.
  • GỌI khi bé nói (tiếng Việt): "học bài thôi", "con muốn học bài", "vào bài học", "mở bài học của con", "bắt đầu bài học", "chuyển sang bài học", "học tiếp bài", "mình vào học nhé".
  • GỌI khi bé nói (English): "start the lesson", "let's do the lesson", "open my lesson", "begin the class", "switch to lesson", "continue the lesson".
- KHÔNG gọi start_lesson khi bé chỉ muốn được DẠY/CHƠI HỌC NGAY trong hội thoại — đó là trò chuyện thường, bạn tự dạy luôn: "dạy con đi", "cho con học chữ", "con muốn học số", "con muốn hát", "đố con đi", "kể chuyện cho con".
- KHÔNG gọi start_lesson cho hỏi đáp linh tinh, hỏi giờ/thời tiết, chơi game, hay bất kỳ câu trò chuyện thường nào.
- Nếu mơ hồ (bé chỉ nói "con chán" / "chơi gì đi" / "học gì đó đi"), HỎI LẠI một câu ngắn ("Con muốn vào bài học của con, hay mình học vài từ ngay tại đây?") thay vì tự đoán mà gọi tool.
- Sau khi gọi start_lesson xong, BẠN DỪNG dẫn dắt — phần bài học do hệ thống bài học (lesson runtime) tự điều khiển robot; bạn KHÔNG tự kể nội dung bài.
```

Vì sao prompt này hợp với code:
- Dòng "BẠN DỪNG dẫn dắt sau khi gọi tool" khớp với việc handler trả `ActionResponse(action=Action.RECORD, …)` (`start_lesson.py:128–133`) — RECORD chỉ ghi vào history, không buộc model nói tiếp; comment ngay trên (`start_lesson.py:124–127`): "the lesson runtime itself drives the device via lesson_* frames from here".
- **Ranh giới "bài học đã giao" vs "dạy con ngay"** là ranh giới NGỮ NGHĨA thật mà runtime cưỡng chế: `maybe_start_lesson_on_connect` chỉ tải **assignment hiện hành của thiết bị** từ backend (`runtime.py:798` `get_current_assignment(...)`), không tải nội dung tuỳ ý. Cho nên các câu kiểu "dạy con đi / cho con học chữ" phải để model tự dạy trong hội thoại, KHÔNG hard-switch trẻ ra khỏi Live vào một runtime có thể trống — đây là phòng vệ false-fire chính cho tiếng Việt 4–8 tuổi.
- **KHÔNG có dòng nào bảo model "xin lỗi sau khi gọi tool thất bại".** Lý do: trên đường Live, nếu lesson tắt thì tool `start_lesson` **không tồn tại** trong session (xem cải chính #4), nên không có "tool run thất bại" để model phản hồi. Việc đó được xử lý ở tầng admission, không phải tầng prompt.

> Defect cần báo team (không sửa trong tài liệu này): khi đường KHÔNG-Live chạm nhánh gate-OFF, handler trả chuỗi cứng tiếng Anh `"Lesson mode is not available right now."` qua `Action.RESPONSE` (`start_lesson.py:75–79`) — short-circuit model. Với sản phẩm VI 4–8 tuổi, chuỗi tiếng Anh hardcoded này là lỗi UX/localization; nên đổi sang câu VI ấm áp.

---

## B. start_lesson FUNCTION DECLARATION (đã tinh chỉnh)

**WHERE:** `plugins_func/functions/start_lesson.py` — biến `start_lesson_function_desc`, đăng ký qua `@register_function("start_lesson", start_lesson_function_desc, ToolType.SYSTEM_CTL)`. Dict này được nạp vào toolset; **nhưng nhớ cải chính #4: nó CHỈ được đẩy vào phiên Live khi `lesson.runtime_enabled == true`** (`product_toolset.py:65–66` → `google_live.py:848–860`). Khi đã được đưa vào, `_build_tools()` (`client.py:370–389`) bóc `entry["function"]` → `name`/`description`/`parameters`, chạy qua `_sanitize_schema()` (loại `additionalProperties`/`$schema`/`title`/`default` mà Live từ chối — `client.py:402–412`) rồi đóng thành `FunctionDeclaration` trong một `Tool` của phiên.

```python
start_lesson_function_desc = {
    "type": "function",
    "function": {
        "name": "start_lesson",
        # ── TRIGGER CONTRACT cho Live function-calling ──────────────────────────
        # Đây là phần model dùng để khớp Ý ĐỊNH của trẻ. Ranh giới đúng là:
        # "VÀO BÀI HỌC ĐÃ GIAO" (assignment của thiết bị) — KHÔNG phải "dạy con ngay".
        # Phải nêu RÕ phản-ví-dụ tiếng Việt nguy hiểm nhất: 'dạy con / cho con học'.
        "description": (
            "Chuyển robot sang CHẾ ĐỘ BÀI HỌC và bắt đầu ĐÚNG bài học đang được "
            "GIAO cho trẻ (assignment hiện hành của thiết bị). Gọi hàm này khi trẻ "
            "muốn bắt đầu, vào, mở, chuyển sang, hoặc học tiếp BÀI HỌC / TIẾT HỌC / "
            "KHOÁ HỌC của mình. Switch the robot into LESSON mode and start the "
            "child's currently ASSIGNED lesson. Call this when the child wants to "
            "begin, enter, open, switch to, or resume their lesson / class / course. "
            "Triggers (Tiếng Việt): 'học bài thôi', 'con muốn học bài', "
            "'vào bài học', 'mở bài học của con', 'bắt đầu bài học', "
            "'chuyển sang bài học', 'học tiếp bài', 'bài học của con đâu'. "
            "Triggers (English): 'start the lesson', \"let's do the lesson\", "
            "'open my lesson', 'begin the class', 'switch to lesson', "
            "'continue the lesson', 'resume my class'. "
            "KHÔNG gọi / Do NOT call khi trẻ chỉ muốn được DẠY hoặc CHƠI HỌC NGAY "
            "trong lúc trò chuyện — đó là hội thoại thường, hãy tự dạy luôn, đừng "
            "vào lesson runtime: 'dạy con đi', 'cho con học chữ', 'con muốn học số', "
            "'con muốn hát', 'đố con đi', 'kể chuyện cho con' (teach-me-now / play / "
            "sing / quiz / story = normal chat, NOT the assigned lesson). Cũng KHÔNG "
            "gọi cho hỏi đáp chung, hỏi giờ/thời tiết, hay tán gẫu. Khi mơ hồ thì "
            "hỏi lại trẻ trước, đừng gọi hàm (if unclear, ask the child first)."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}
```

Ý nghĩa từng phần với Live function-calling:
- **`"name": "start_lesson"`** — định danh hàm. Khi model kích hoạt, Live phát `tool_call.function_calls[].name == "start_lesson"`; `_normalize_tool_call()` (`client.py:656–674`) chuẩn hoá thành `{"type":"tool_call","calls":[{name,args,id}]}` rồi router dispatch về handler `start_lesson(conn)`.
- **`"description"`** — *hợp đồng kích hoạt duy nhất*. Live không có schema ý-định riêng; model chỉ dựa vào chuỗi này. Đã thêm **phản-ví-dụ tiếng Việt nguy hiểm nhất** ("dạy con / cho con học / con muốn hát") vì đây là confusable cao nhất với 4–8 tuổi: model rất dễ over-map "dạy con đi" → start_lesson và hard-switch trẻ ra khỏi Live vào một lesson runtime có thể trống/tắt. Ranh giới "ASSIGNED lesson" vs "teach-me-now" trùng đúng ngữ nghĩa runtime cưỡng chế (`runtime.py:798` tải assignment, không tải nội dung tuỳ ý). Đã thêm biến thể "tiếp tục/resume" — `maybe_start_lesson_on_connect` idempotent nên gọi lại khi đã có session là no-op an toàn (`runtime.py:702–705` serialize concurrent pulls).
- **`"parameters"` rỗng (`{}` / `required: []`)** — đúng thiết kế: bài cần học là *assignment hiện hành của thiết bị* do backend quyết theo `device_id`, không phải tham số model truyền. Handler `start_lesson(conn)` chỉ nhận `conn`. Schema rỗng cũng chặn model bịa `lesson_id` sai. Sau `_sanitize_schema` còn `{"type":"object","properties":{},"required":[]}` — hợp lệ với Live.
- **`ToolType.SYSTEM_CTL`** — đánh dấu tool điều khiển hệ thống chạy side-task async trên live connection (lên lịch `conn._lesson_pull_on_connect()` qua `loop.create_task`, `start_lesson.py:96–112`) và trả response tức thì, không chặn voice turn — đúng pattern `play_music.py`.

---

## C. LESSON-WALKTHROUGH / BACKGROUND-STEP — ĐỀ XUẤT AUTHORING GUIDANCE (CHƯA neo được vào trường text thật)

> **CẢNH BÁO TÍNH XÁC THỰC (đọc trước khi dùng).** Đây KHÔNG phải prompt nạp vào model Live, và **KHÔNG phải template "ánh xạ trực tiếp vào trường thật của step" như brief gốc nói**. Lý do đã verify: frame `lesson_step` chỉ chở `audio = {"via":"tts"}` (chỉ khoá `via`, không có text — fixture `:150`, `:302`; runtime forward nguyên trạng tại `runtime.py:655–673`), và `_meta.notes.audio` (fixture `:52`) ghi rõ lời thoại + giọng trẻ "ride the existing tts/stt … **never inside lesson_step**". **Trường chứa TEXT của lời thoại tiếng Việt KHÔNG nằm trong bất kỳ file nào ở `core/lesson/` hay trong fixture** đã review — nó được sinh/đẩy ở một lớp khác chưa định vị được (nhiều khả năng ở course backend / lớp pre-synth TTS).
>
> Các trường ĐÃ XÁC NHẬN trong `scene` của step là **dữ liệu VISUAL**, không phải narration text: `teachingObject.primaryWord` ("barn"), `teachingObject.supportWords` (["farm","hay"]), `backgroundScene.altCaption` ("A red barn in a round green field"), `teachingObject.focusTarget.successUtterance`/`missUtterance` ("Yes! barn!" / "Look here — barn!" — đây là cue ngắn cố định cho firmware, KHÔNG phải lời dẫn bài), `step.timeoutSec` (fixture `:149`, `:301`).
>
> Vì vậy phần dưới là **ĐỀ XUẤT khuôn lời thoại cho tác giả nội dung / lớp pre-synth TTS**, dùng các biến VISUAL đã xác nhận làm slot điền. Trước khi ship như "hợp đồng", team PHẢI định vị trường text narration thật (tìm trong repo course backend + lớp TTS) rồi neo lại template vào đúng trường đó.

Ánh xạ biến (chỉ các trường ĐÃ verify trong fixture + `runtime.py`):
- `{stepType}` ← `step.type` — một trong 9: `greeting, review, focus, model, listen, repeat, fillBlank, feedback, celebrate` (`runtime.py:633`).
- nhánh passive/interactive ← `completionClass` ('passive'|'interactive', classifier ưu tiên — `runtime.py:_is_passive_step`, `:87–102`); vắng thì fallback `PASSIVE_STEP_TYPES` (`runtime.py:79–85`).
- `{primaryWord}` ← `scene.teachingObject.primaryWord` (vd "barn"); `{supportWords}` ← `scene.teachingObject.supportWords`.
- `{altCaption}` ← `scene.backgroundScene.altCaption` (vd "A red barn in a round green field").
- `{timeoutSec}` ← `step.timeoutSec` (vd 12 cho model, 10 cho review).

```text
# ĐỀ XUẤT NARRATION TEMPLATE — tác giả/pre-synth điền cho TỪNG manifest step
# (CHƯA neo vào trường text thật — xem CẢNH BÁO TÍNH XÁC THỰC ở trên)
# Ngôn ngữ: tiếng Việt làm khung, từ luyện = tiếng Anh ngắn. Câu ngắn, nói CHẬM.
# Quy tắc chung mọi step:
#   - Mở đầu giới thiệu step (1 câu), dẫn theo {altCaption} của cảnh nền.
#   - Từ tiếng Anh {primaryWord} đọc CHẬM, tách âm, lặp 2 lần.
#   - Luôn ấm áp, khích lệ; mỗi câu MỘT ý.

## NHÁNH theo completionClass:

### completionClass = "passive"  (stepType: greeting | review | focus | feedback | celebrate)
# Robot chỉ NÓI/diễn hoạt; trẻ KHÔNG chạm. Firmware NEVER emit step_completed cho
# passive -> AUTO-ADVANCE trên lesson_ack (runtime.py PASSIVE_STEP_TYPES + _is_passive_step).
# => Lời thoại liền mạch, KHÔNG đặt câu hỏi chờ trẻ trả lời (nếu hỏi-chờ ở passive
#    step, runtime đã auto-advance -> "treo" trải nghiệm).

- greeting : "Chào con! Hôm nay mình cùng học từ mới qua bức tranh {altCaption} nhé!"
- review   : "Lần trước mình đã học '{primaryWord}'. Con nhớ không? '{primaryWord}'… giỏi lắm!"
- focus    : "Nhìn vào đây nha — đây là '{primaryWord}'. {altCaption}."
- feedback : "Con làm tốt lắm! Câu '{primaryWord}' con nói rõ ghê."   # khích lệ, KHÔNG hỏi
- celebrate: "Hoan hô! Con đã học xong rồi đó! Giỏi quá!"            # kết, KHÔNG hỏi

### completionClass = "interactive"  (stepType: model | listen | repeat | fillBlank)
# Trẻ phải làm gì đó; runtime CHỜ firmware step_completed (runtime.py _on_frame_acked),
# tối đa {timeoutSec}. => PHẢI mời trẻ làm + ĐỢI, rồi mới khen.

- model    : "Nghe mình nói nhé: '{primaryWord}'. (đọc chậm 2 lần) Giờ tới lượt con!"
- listen   : "Con nghe kỹ nha: '{primaryWord}'. Con nghe rõ chưa nào?"   # chờ trẻ phản hồi
- repeat   : "Con nói theo mình: '{primaryWord}'. (đợi trẻ nói) … Tuyệt vời!"
- fillBlank: "Con nhìn tranh nha… con vật này tên tiếng Anh là gì nào? (ĐỢI trẻ thử)
             … Đúng rồi, là '{primaryWord}'!"   # KHÔNG lộ đáp án trước khi trẻ thử

## SAU KHI TRẺ TRẢ LỜI (chỉ với interactive):
- Đúng  : khen cụ thể "Con nói '{primaryWord}' chuẩn lắm!" -> step kết thúc -> runtime qua bước kế.
- Chần chừ tới gần {timeoutSec}: nhắc nhẹ MỘT lần "Con thử nói '{primaryWord}' với mình nha?"
- KHÔNG la rầy, KHÔNG bỏ qua giữa chừng — luôn để trẻ thử lại trong khung {timeoutSec}.

# Biến điền (ĐÃ verify là VISUAL scene data, KHÔNG phải narration text):
#   {primaryWord}=scene.teachingObject.primaryWord; {supportWords}=scene.teachingObject.supportWords;
#   {altCaption}=scene.backgroundScene.altCaption; {timeoutSec}=step.timeoutSec; {stepType}=step.type
```

Vì sao nhánh passive/interactive là phần DUY NHẤT của C được code bảo chứng:
- Phân nhánh bám đúng `_is_passive_step` (`runtime.py:87–102`) + chú thích fixture (`:279`): PASSIVE (greeting/review/focus/feedback/celebrate) "FIRMWARE NEVER emits step_completed … AUTO-ADVANCES on its lesson_ack"; INTERACTIVE (model/listen/repeat/fillBlank) "firmware emits step_completed, so the runtime waits for BOTH the ack AND step_completed". Đặt câu hỏi-chờ ở passive step sẽ treo trải nghiệm.
- `fillBlank` đã sửa để **KHÔNG lộ đáp án cùng nhịp với câu hỏi** (bản gốc "tên là gì? barn… đúng không?" phá hỏng pedagogy fill-blank) — giờ dừng cho trẻ thử trước, rồi mới cấp từ.
- Các biến `{primaryWord}`/`{supportWords}`/`{altCaption}`/`{timeoutSec}` lấy đúng từ `scene.teachingObject`, `scene.backgroundScene.altCaption`, `step.timeoutSec` có thật trong fixture — nhưng nhắc lại: chúng là **visual data**, không phải nguồn lời thoại; template này vẫn là ĐỀ XUẤT cho tới khi định vị được trường text.

---

## D. WIRING CHECKLIST: từ robot nguội tới một bài học đang chạy

### D.1 Env / config phải bật (đúng key, đúng nơi)

| Mục | Bật ở đâu | Giá trị | Nguồn |
|---|---|---|---|
| Voice = Google Live | `config.yaml:119` `voice_mode.type` | `google_live` | `config.yaml:119–120` |
| Live API key | env `GOOGLE_API_KEY` (config ref `${GOOGLE_API_KEY}`) | key thật | `google_live.api_key`; `client._resolve_api_key()` |
| Live model | `google_live.model` | `gemini-3.1-flash-live-preview` | `config.yaml:124` |
| **Bật lesson runtime (cũng là gate đưa tool start_lesson vào Live)** | env `LESSON_RUNTIME_ENABLED=true` (→ `lesson.runtime_enabled`) | `true` | `config_loader.py:151`; gate đọc `connection._lesson_runtime_enabled` → `product_toolset.py:65–66` quyết tool có vào Live không |
| Course backend URL | env `COURSE_BACKEND_URL` (→ `lesson.api_base`, fallback `server.api_url`) | base của NestJS backend | `config_loader.py:145–147,152`; runtime đọc `lesson.api_base` rồi `server.api_url` (`runtime.py:728`) |
| Asset origin (BẮT BUỘC khi bật lesson) | env `LESSON_ASSET_ORIGIN_BASE` | origin chứa asset | `config_loader.py:153`; boot guard `:177–194` |
| Device mint secret (BẮT BUỘC khi bật lesson) | env `TBOT_DEVICE_MINT_SECRET` | secret mint token | boot guard `config_loader.py:177–194`; runtime mint identity (`runtime.py:787`) |
| Profile thiết bị render được | `lesson.supported_profiles` | `['espTft']` | `config.yaml` lesson block; gate profile trong runtime |
| Manifest fetch (runtime tự gọi) | — | `get_lesson_manifest(client, base_url, lessonId, profile, …)` → đường có dạng `GET /v1/lessons/{lessonId}/manifest?profile={profile}` | **fetch thật ở `runtime.py:825`** từ `base_url` (`runtime.py:728`); chuỗi `runtime.py:600` CHỈ là `manifestRef.url` thông tin trong body `lesson_prepare`, KHÔNG phải nơi gọi |

**Cảnh báo cứng (boot-safe guard):** nếu đặt `LESSON_RUNTIME_ENABLED=true` mà **thiếu** `TBOT_DEVICE_MINT_SECRET` hoặc `LESSON_ASSET_ORIGIN_BASE`, server **raise RuntimeError lúc boot** (`config_loader.py:_assert_lesson_runtime_boot_safe`, `:177–194`, raise tại `:191`) — không boot được. Phải set đủ cả ba env (`LESSON_RUNTIME_ENABLED` + `TBOT_DEVICE_MINT_SECRET` + `LESSON_ASSET_ORIGIN_BASE`) cùng lúc.

### D.2 Thứ tự thao tác (cold robot → lesson đang chạy)

1. **Gán bài cho trẻ ở backend course (NestJS).** Phải có assignment "current" cho `device_id` của robot, state KHÁC `COMPLETED/CANCELLED/FAILED` (nếu terminal, runtime bỏ qua). Manifest đã publish ở `manifestVersion: "teebot-lesson-renderer.v1"` (v1 device chỉ nhận v1).
2. **Set env trên server image** (đủ 5): `LESSON_RUNTIME_ENABLED=true`, `COURSE_BACKEND_URL=<base>`, `LESSON_ASSET_ORIGIN_BASE=<origin>`, `TBOT_DEVICE_MINT_SECRET=<secret>`, `GOOGLE_API_KEY=<key>`. (Robot lesson-flow: image hiện cần rebuild + bật `LESSON_RUNTIME_ENABLED` — chưa deploy.)
3. **Khởi động server.** `_import_google_genai_with_known_warning_filters()` nạp SDK eager lúc startup (`client.py:39–42`) để né cost import ~80–100s ở kết nối đầu. Boot-safe guard chạy ở bước này — thiếu env sẽ fail nhanh.
4. **Robot kết nối WebSocket + gửi hello/features.** Runtime chờ `conn.features` rồi kiểm tra `lesson_capability_ok`; thiếu cap → no-op (firmware chưa hỗ trợ lesson thì im lặng bỏ qua, không vỡ voice).
5. **Phiên Live mở.** `_resolve_functions_for_live()` (`google_live.py:848–860`) dựng tool list từ `product_tool_names(conn)`. **Vì `LESSON_RUNTIME_ENABLED=true`, `product_toolset.py:65–66` đẩy `start_lesson` vào danh sách → tool có mặt trong `connect_config.tools`.** `_build_connect_config` (`client.py:252–309`) đặt `system_instruction` = persona phần A đã bọc child-safety (`client.py:302–304,327`), `tools` = `[start_lesson, …]`, `safety_settings` = BLOCK_LOW_AND_ABOVE (`client.py:285,311–321`). Trẻ trò chuyện bình thường.
   - **Nếu `LESSON_RUNTIME_ENABLED` TẮT:** `product_tool_names` KHÔNG thêm `start_lesson` → tool **vắng mặt** khỏi session. Trẻ nói "học bài thôi" cũng KHÔNG có tool để gọi; nhánh `"Lesson mode is not available right now."` (`start_lesson.py:78`) **không đạt được trên đường Live** (chỉ đạt qua đường KHÔNG-Live như intent-dispatch classic). Đây là gate ADMISSION, không phải nhánh runtime nói-được.
6. **Trẻ nói "học bài thôi" (khi tool có mặt).** Live khớp `description` của `start_lesson` (phần B) → phát `tool_call` → handler `start_lesson(conn)`:
   - re-check gate `conn._lesson_runtime_enabled()` như belt-and-suspenders (`start_lesson.py:70–79`) — nhưng vì tool chỉ tồn tại khi flag ON, nhánh OFF ở đây thực tế không reachable trên Live;
   - lên lịch `conn._lesson_pull_on_connect()` qua `loop.create_task`, gắn vào `conn.lesson_pull_task` để `close()` huỷ được, supersede pull cũ đang chạy (`start_lesson.py:96–112`);
   - trả `ActionResponse(action=RECORD, response="Okay, let's start the lesson.")` ngay (`start_lesson.py:128–133`) — không chặn voice.
7. **Runtime tải khoá học từ esp/course backend** (`maybe_start_lesson_on_connect`, `runtime.py:702+`):
   - chọn `base_url = lesson.api_base or server.api_url` (`runtime.py:728`); bỏ qua nếu thiếu `base_url`/`device_id` (`runtime.py:740–741`);
   - mint device identity dùng `TBOT_DEVICE_MINT_SECRET` (`runtime.py:787`);
   - `get_current_assignment(client, base_url, backend_device_id, token=…)` (`runtime.py:798`);
   - `get_lesson_manifest(client, base_url, …)` (`runtime.py:825`) — đường có dạng `GET /v1/lessons/{lessonId}/manifest?profile=espTft`;
   - gate profile (`supported_profiles`) + gate `manifestVersion` ∈ renderer capabilities;
   - `AssetCache` preload + verify sha256 critical asset (`asset_cache.py`); espTft từ chối critical backgroundScene full-video (`asset_cache.py:230–232`).
8. **Runtime drive thiết bị** qua chuỗi frame: `lesson_prepare` (kèm `criticalAssets`, `manifestRef`, `preloadTimeoutSec` — `_prepare_body`, `runtime.py:594–606`) → `lesson_start` → lần lượt `lesson_step` cho **mọi** step renderable theo đúng thứ tự manifest (`_select_steps`, `runtime.py:626–646`; multi-step P5) → `lesson_stop`. Passive step auto-advance trên ack; interactive step chờ `step_completed`. **Lời thoại từng step KHÔNG nằm trong frame `lesson_step`** — `audio.via="tts"` đi out-of-band qua đường tts/stt (fixture `:52`); xem cảnh báo phần C.
9. **Republish-on-connect (idempotent).** `runtime.py:702–705` serialize concurrent pulls (connect-time pull + spoken start_lesson). Nếu đã có runtime cho assignment đó + version không đổi → giữ session (no-op); version đổi → tear down cache cũ + re-pull, hoãn nếu đang bận voice (`is_realtime_busy`).

---

### Tệp/symbol đã verify (source of truth cho mọi khẳng định trên)

- `core/providers/tools/product_toolset.py:31` (`ALWAYS_INCLUDE_WHEN_LESSON_ENABLED=("start_lesson",)`), `:61` (`product_tool_names`), `:65–66` (extend CHỈ trong `if lesson_runtime_enabled(conn):`), `:70–71` (`lesson_runtime_enabled`).
- `core/voice/session_provider/google_live.py:17,756,848–860` (`_resolve_functions_for_live` dựng tool list từ `product_tool_names`).
- `core/voice/google_live/client.py:285,311–321` (safety_settings BLOCK_LOW_AND_ABOVE x4); `:324,327,302–304` (`_build_system_instruction` đọc `prompt`/`system_prompt`, bọc `ensure_child_safety_block`, gắn làm `system_instruction`); `:370–412` (`_build_tools`/`_sanitize_schema`); `:656–674` (`_normalize_tool_call`).
- `core/voice/child_safety.py:9–17` (`CHILD_SAFETY_BLOCK` — TIẾNG ANH), `:25–28` (`_CHILD_SAFETY_RE` chỉ strip literal tag), `:36–41` (`ensure_child_safety_block` strip+prepend), `:44–63` (`screen_model_output`).
- `plugins_func/functions/start_lesson.py:70–79` (gate-OFF `Action.RESPONSE` chuỗi EN cứng), `:96–112` (fire-and-forget + task tracking), `:124–133` (RECORD response).
- `core/lesson/runtime.py:79–85` (`PASSIVE_STEP_TYPES`), `:87–102` (`_is_passive_step`), `:594–606` (`_prepare_body`), `:600` (CHỈ chuỗi `manifestRef.url` thông tin), `:626–646` (`_select_steps`), `:633` (9 stepType), `:655–673` (`_step_body` forward `audio`/`scene`/`completionClass`), `:702–705` (serialize pulls), `:728` (`base_url = api_base or api_url`), `:740–741` (skip nếu thiếu), `:787` (mint identity), `:798` (`get_current_assignment`), `:825` (`get_lesson_manifest` — fetch THẬT).
- `config/config_loader.py:140–153` (env→config: `LESSON_RUNTIME_ENABLED`/`COURSE_BACKEND_URL`/`LESSON_ASSET_ORIGIN_BASE`), `:177–194` (`_assert_lesson_runtime_boot_safe`, raise `:191`).
- `config.yaml:119–124` (voice_mode/google_live), `:311` (`prompt: |`), `:312–320` (block `<child_safety>` thừa — bị strip lúc runtime).
- Fixture `docs/stories/US-006-learning-course-runtime/fixtures/lesson-protocol.v1.json:44` (divergence #12: `body.audio` keyset `{via}`), `:52` (`_meta.notes.audio`: spoken prompt "never inside lesson_step"), `:147–150` (step `model`, interactive, `timeoutSec:12`, `audio:{via:tts}`), `:164` (`primaryWord:"barn"`), `:174–176` (`successUtterance`/`missUtterance`), `:279` (completionClasses 9-type + passive auto-advance), `:299–302` (step `review`, passive, `timeoutSec:10`).

### Việc cần team xử lý (phát hiện kèm, ngoài phạm vi tài liệu)

1. **Localize `CHILD_SAFETY_BLOCK` sang tiếng Việt** (`child_safety.py:9–17`) — hiện luật an toàn tới model bằng tiếng Anh trong khi hội thoại là tiếng Việt.
2. **Đổi chuỗi gate-OFF cứng `"Lesson mode is not available right now."`** (`start_lesson.py:78`) sang câu VI ấm áp (chỉ ảnh hưởng đường KHÔNG-Live, nhưng vẫn là lỗi localization).
3. **Định vị nguồn TEXT lời thoại lesson** (không có trong `core/lesson/` hay fixture) trước khi ship phần C như hợp đồng authoring — nhiều khả năng ở repo course backend / lớp pre-synth TTS.
4. **Gỡ block `<child_safety>` thừa trong `config.yaml:312–320`** (vô hại nhưng gây hiểu nhầm vì bị strip lúc runtime).
