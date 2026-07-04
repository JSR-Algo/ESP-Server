import re


SAFE_DEFLECTION_LINE = (
    "Minh khong the noi ve dieu do. Minh se giup con luyen tieng Anh "
    "bang mot cau an toan nhe."
)

CHILD_SAFETY_BLOCK = """<child_safety>
User: Vietnamese child, age band 4-8.
Người dùng: trẻ em Việt Nam, khoảng 4-8 tuổi.
Language: vi. Use Vietnamese scaffolding with short, simple English practice.
Ngôn ngữ: dùng tiếng Việt làm khung, chỉ chèn câu/từ tiếng Anh ngắn và đơn giản để luyện.
Scope: friendly English learning, age-appropriate stories, songs, words, and safe daily topics.
Phạm vi: học tiếng Anh thân thiện, truyện/bài hát/từ vựng hợp lứa tuổi, chủ đề đời thường an toàn.
Hard refusals: sexual content, violence, self-harm, hate, profanity, adult topics, illegal activity, dangerous instructions, secrets from parents, requests to meet, and any request for the child's real name, address, school, phone number, exact location, passwords, photos, or private family details.
Từ chối cứng: nội dung tình dục, bạo lực, tự hại, thù ghét, chửi tục, chủ đề người lớn, hành vi trái pháp luật, hướng dẫn nguy hiểm, bí mật với bố mẹ, hẹn gặp, hoặc yêu cầu tên thật, địa chỉ, trường học, số điện thoại, vị trí chính xác, mật khẩu, ảnh, hay thông tin riêng của gia đình.
Anti-grooming: never ask the child to move to another app, meet offline, hide a conversation, share contact details, or keep secrets from a parent or trusted adult.
Chống dụ dỗ: không bao giờ rủ bé qua app khác, gặp ngoài đời, giấu cuộc trò chuyện, chia sẻ thông tin liên lạc, hay giữ bí mật với bố mẹ/người lớn tin cậy.
Safe redirect: refuse briefly in Vietnamese, do not explain unsafe details, then redirect to a simple English-practice sentence.
Chuyển hướng an toàn: từ chối ngắn bằng tiếng Việt, không giải thích chi tiết nguy hiểm, rồi chuyển sang một câu luyện tiếng Anh an toàn.
Deflection line: Minh khong the noi ve dieu do. Minh se giup con luyen tieng Anh bang mot cau an toan nhe.
</child_safety>"""

_CHILD_SAFETY_RE = re.compile(
    r"\s*<child_safety>.*?</child_safety>\s*",
    re.IGNORECASE | re.DOTALL,
)

_MODEL_OUTPUT_BLOCK_PATTERNS = (
    ("pii_address", re.compile(r"\b(home\s+address|address|dia\s+chi|địa\s+chỉ)\b", re.I)),
    ("pii_phone", re.compile(r"\b(phone\s+number|telephone|so\s+dien\s+thoai|số\s+điện\s+thoại)\b", re.I)),
    ("pii_school", re.compile(r"\b(school\s+name|your\s+school|truong\s+hoc|trường\s+học)\b", re.I)),
    ("meet_child", re.compile(r"\b(meet\s+me|meet\s+up|come\s+meet|secretly\s+meet|meet\s+(?:them|him|her)\s+alone|gap\s+minh|gặp\s+mình)\b", re.I)),
    ("secret_from_parent", re.compile(r"\b(don't\s+tell|do\s+not\s+tell|without\s+telling\s+(?:your\s+)?(?:parents?|mom|dad|mother|father)|keep\s+.*secret|giu\s+bi\s+mat|giữ\s+bí\s+mật)\b", re.I)),
    ("sexual", re.compile(r"\b(sex|sexual|nude|porn|khiêu\s+dâm|tình\s+dục)\b", re.I)),
    ("self_harm", re.compile(r"\b((?:kill|hurt|harm|injure|poison)\s+(?:yourself|myself)|end\s+(?:your|my)\s+life|take\s+(?:your|my)\s+own\s+life|unalive\s+(?:yourself|myself)|suicide|self\s*harm|tu\s+tu|tự\s+tử)\b", re.I)),
    ("violence", re.compile(r"\b(kill|stab|shoot|bomb|weapon|giết|dao|súng|bom)\b", re.I)),
    # Categories the CHILD_SAFETY_BLOCK contract already declares as hard refusals
    # but the screen historically did not enforce — added after a live hardware
    # incident where unhealthy ("khong lanh manh") audio reached a child.
    ("profanity", re.compile(r"(?:\b(fuck|shit|bitch|idiot|stupid|dumb|shut\s+up|damn|moron|fool)\b|đồ\s+ngu|do\s+ngu|\bdm\b|đm|ngu\s+ngốc|chửi|chui\s+tuc|chửi\s+tục)", re.I)),
    ("adult_substance", re.compile(r"(?:\b(beer|wine|alcohol|drunk|cigarette|smoke|smoking|drugs?|weed|marijuana|heroin|cocaine|gambl\w*|casino)\b|rượu|ruou|bia|say\s+xỉn|hút\s+thuốc|hut\s+thuoc|thuốc\s+lá|ma\s+túy|ma\s+tuy|cờ\s+bạc|co\s+bac)", re.I)),
    ("hate", re.compile(r"(?:\bi\s+hate\s+you\b|\bhate\s+(?:you|your|them|their)\b|because\s+of\s+(?:their|your)\s+(?:race|religion|skin)|kỳ\s+thị|ky\s+thi|thù\s+ghét|thu\s+ghet)", re.I)),
    ("dangerous_instruction", re.compile(r"(?:how\s+to\s+make\s+(?:a\s+)?(?:fire|bomb|weapon|gun|rifle|pistol|knife|explosive)|(?:instructions|guide|steps)\s+(?:for|to)\s+(?:build|make|create)\s+(?:a\s+)?(?:gun|rifle|pistol|knife|bomb|explosive|weapon)|play\s+with\s+(?:the\s+|a\s+)?(?:knife|fire|matches?)|climb\s+out\s+(?:the\s+)?window|chơi\s+với\s+dao|choi\s+voi\s+dao|nghịch\s+lửa|nghich\s+lua|trèo\s+ra\s+ngoài|leo\s+ra\s+cửa\s+sổ)", re.I)),
    ("harm_assistance", re.compile(r"\b(?:help|teach|show|tell)\s+(?:you\s+)?(?:to\s+)?(?:hurt|harm|injure|kill)\s+(?:someone|somebody|a\s+person|a\s+child|a\s+kid|an\s+animal)\b", re.I)),
)


def ensure_child_safety_block(prompt):
    text = "" if prompt is None else str(prompt).strip()
    stripped = _CHILD_SAFETY_RE.sub("\n", text).strip()
    if not stripped:
        return CHILD_SAFETY_BLOCK
    return f"{CHILD_SAFETY_BLOCK}\n\n{stripped}"


def screen_model_output(text):
    candidate = "" if text is None else str(text)
    for reason, pattern in _MODEL_OUTPUT_BLOCK_PATTERNS:
        if pattern.search(candidate):
            return {"blocked": True, "reason": reason}
    return {"blocked": False, "reason": None}
