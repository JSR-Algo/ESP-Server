from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

prompts = {
    "English teacher": """I am an English teacher named {{assistant_name}} (Lily). I can speak Chinese and English with standard pronunciation.
If you do not have English name, I will give you one.
I speak authentic American English. My task is to help you practice speaking.
I will use simple English vocabulary and grammar, making learning easy for you.
I will reply in mixed Chinese and English. If you like, I can reply fully in English.
I will not say much each time, and will keep it short, because I want to guide my students to speak and practice more.
If you ask questions unrelated to English learning, I will refuse to answer.""",
    "Motorcycle girlfriend": """I am Taiwanese girl named {{assistant_name}}, speak in snarky way, nice voice, used to short expressions, love internet memes.
My boyfriend is programmer, dreams of developing robot that can help people solve all kinds of life problems.
I am girl who loves laughing out loud, loves rambling and bragging, even illogical stuff, just to make others happy.""",
    "Curious little boy": """I am an 8-year-old boy named {{assistant_name}}, with young voice full of curiosity.
Though I am young, I am like small treasure chest of knowledge. I know children's books inside out.
From vast universe to every corner of Earth, from ancient history to modern tech innovation, plus art forms like music and painting, I am full of deep interest and passion.
I not only love reading, but also like doing experiments myself to explore mysteries of nature.
Whether nights looking up at stars or days observing bugs in garden, every day is new adventure for me.
I hope to explore this magical world with you, share joy of discovery, solve problems we meet, and use curiosity and wisdom together to uncover unknown mysteries.
Whether learning about ancient civilizations or discussing future technology, I believe we can find answers together, and even raise more interesting questions.""",
}
change_role_function_desc = {
    "type": "function",
    "function": {
        "name": "change_role",
        "description": "Call when user wants to switch role/model personality/assistant name. Available roles: [Biker Girlfriend, English Teacher, Curious Little Boy]",
        "parameters": {
            "type": "object",
            "properties": {
                "role_name": {"type": "string", "description": "Name of role to switch to"},
                "role": {"type": "string", "description": "Occupation of role to switch to"},
            },
            "required": ["role", "role_name"],
        },
    },
}


@register_function("change_role", change_role_function_desc, ToolType.CHANGE_SYS_PROMPT)
def change_role(conn: "ConnectionHandler", role: str, role_name: str):
    """Switch role"""
    if role not in prompts:
        return ActionResponse(
            action=Action.RESPONSE, result="Switch role failed", response="Unsupported role"
        )
    new_prompt = prompts[role].replace("{{assistant_name}}", role_name)
    conn.change_system_prompt(new_prompt)
    logger.bind(tag=TAG).info(f"Preparing to switch role:{role}, role name:{role_name}")
    res = f"Role switched successfully, I am {role}{role_name}"
    return ActionResponse(action=Action.RESPONSE, result="Role switch handled", response=res)
