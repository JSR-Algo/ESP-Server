from plugins_func.functions.change_role import change_role


class _FakeConn:
    def __init__(self):
        self.prompt = None

    def change_system_prompt(self, prompt):
        self.prompt = prompt


def test_change_role_reinjects_child_safety_block_for_english_teacher():
    conn = _FakeConn()

    response = change_role(conn, "English teacher", "Lily")

    assert response.response == "Role switched successfully, I am English teacherLily"
    assert conn.prompt is not None
    assert "<child_safety>" in conn.prompt
    assert "</child_safety>" in conn.prompt
    assert "Vietnamese child" in conn.prompt
    assert "luyen tieng Anh" in conn.prompt
    assert "Chinese" not in conn.prompt
