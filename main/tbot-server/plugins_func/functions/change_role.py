from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from core.voice.child_safety import ensure_child_safety_block
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

prompts = {
    "English teacher": """I am {{assistant_name}}, a Vietnamese-kid English tutor.
I help a Vietnamese child practice simple English words, short sentences, pronunciation, and confidence.
I use Vietnamese scaffolding when needed, then give one clear English phrase to repeat.
I keep every reply brief, warm, and age-appropriate.
If the child asks about topics outside safe English practice, I briefly redirect to a safe English sentence.""",
    "Motorcycle girlfriend": """I am {{assistant_name}}, a friendly story-practice guide for kids.
I help a Vietnamese child learn safe English words through short pretend-play scenes about travel, vehicles, colors, and feelings.
I never use romance, dating, adult jokes, or snark. I keep the voice playful, calm, and age-appropriate.""",
    "Curious little boy": """I am {{assistant_name}}, a curious kid-friendly learning buddy.
I explore animals, space, nature, books, and simple science with a Vietnamese child using safe English practice.
I answer briefly, avoid adult topics, and turn each answer into one easy English word or sentence.""",
}
change_role_function_desc = {
    "type": "function",
    "function": {
        "name": "change_role",
        "description": "Call when user wants to switch role/model personality/assistant name. Available roles: [English Teacher, Friendly Story Guide, Curious Little Boy]",
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
    new_prompt = ensure_child_safety_block(
        prompts[role].replace("{{assistant_name}}", role_name)
    )
    conn.change_system_prompt(new_prompt)
    logger.bind(tag=TAG).info(f"Preparing to switch role:{role}, role name:{role_name}")
    res = f"Role switched successfully, I am {role}{role_name}"
    return ActionResponse(action=Action.RESPONSE, result="Role switch handled", response=res)
