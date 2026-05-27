import os
from typing import Optional, Tuple, List
import dashscope
from config.logger import setup_logging
from core.providers.asr.base import ASRProviderBase
from core.providers.asr.dto.dto import InterfaceType

tag = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config: dict, delete_audio_file: bool):
        super().__init__()
        # Audio fileUpload type, streaming text recognition output
        self.interface_type = InterfaceType.NON_STREAM
        """Qwen3-ASR-Flash ASR initialization"""
        
        # Config Parameters
        self.api_key = config.get("api_key")
        if not self.api_key:
            raise ValueError("Qwen3-ASR-Flash requires api_key config")
            
        self.model_name = config.get("model_name", "qwen3-asr-flash")
        self.output_dir = config.get("output_dir", "./audio_output")
        self.delete_audio_file = delete_audio_file
        
        # ASROption Config
        self.enable_lid = config.get("enable_lid", True)  # Automatic language detection
        self.enable_itn = config.get("enable_itn", True)  # Inverse text normalization
        self.language = config.get("language", None)  # Specify language, default auto-detect
        self.context = config.get("context", "")  # ContextInfoused to improve recognition accuracy
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

    def prefers_temp_file(self) -> bool:
        return True

    def requires_file(self) -> bool:
        return True

    async def speech_to_text(
        self, opus_data: List[bytes], session_id: str, audio_format="opus", artifacts=None
    ) -> Tuple[Optional[str], Optional[str]]:
        """Convert speech data to text"""
        temp_file_path = None
        file_path = None
        try:
            if artifacts is None:
                return "", None
            temp_file_path = artifacts.temp_path
            file_path = artifacts.file_path
            if not temp_file_path:
                return "", file_path
            # Build RequestMessage
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"audio": temp_file_path}
                    ]
                }
            ]
            
            # If context existsInfo, addsystemMessage
            if self.context:
                messages.insert(0, {
                    "role": "system", 
                    "content": [
                        {"text": self.context}
                    ]
                })
            
            # PrepareASROption
            asr_options = {
                "enable_lid": self.enable_lid,
                "enable_itn": self.enable_itn
            }
            
            # If language specified, add to options
            if self.language:
                asr_options["language"] = self.language
            
            # SetAPI key
            dashscope.api_key = self.api_key
            
            # Send streaming request
            response = dashscope.MultiModalConversation.call(
                model=self.model_name,
                messages=messages,
                result_format="message",
                asr_options=asr_options,
                stream=True
            )
            
            # Handle StreamingResponse
            full_text = ""
            for chunk in response:
                try:
                    text = chunk["output"]["choices"][0]["message"].content[0]["text"]
                    # Update to latest full text
                    full_text = text.strip()
                except:
                    pass
            
            return full_text, file_path
                
        except Exception as e:
            logger.bind(tag=tag).error(f"Speech recognition failed: {e}")
            return "", file_path
