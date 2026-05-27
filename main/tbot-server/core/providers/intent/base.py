from abc import ABC, abstractmethod
from typing import List, Dict
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


class IntentProviderBase(ABC):
    def __init__(self, config):
        self.config = config

    def set_llm(self, llm):
        self.llm = llm
        # GetModel nameAnd typeInfo
        model_name = getattr(llm, "model_name", str(llm.__class__.__name__))
        # Record more detailed logs
        logger.bind(tag=TAG).info(f"Intent recognition set LLM: {model_name}")

    @abstractmethod
    async def detect_intent(self, conn, dialogue_history: List[Dict], text: str) -> str:
        """
        Detect intent of user's last sentence
        Args:
            dialogue_history: dialogue history list, each record contains role and content
        Returns:
            Returns recognized intent, format:
            - "Continue Chat"
            - "End Chat"
            - "Play music song name" or "Play random music"
            - "Query weather location name" or "Query Weather [Current Location]"
        """
        pass
