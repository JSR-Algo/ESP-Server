from typing import Dict, Any
from config.logger import setup_logging
from core.utils import tts, llm, intent, memory, vad, asr

TAG = __name__
logger = setup_logging()


def initialize_modules(
    logger,
    config: Dict[str, Any],
    init_vad=False,
    init_asr=False,
    init_llm=False,
    init_tts=False,
    init_memory=False,
    init_intent=False,
) -> Dict[str, Any]:
    """
    Initialize all module components

    Args:
        config: config dict

    Returns:
        Dict[str, Any]: dict containing all initialized modules
    """
    modules = {}

    # Initialize TTS module
    if init_tts:
        select_tts_module = _selected_module(config, "TTS")
        modules["tts"] = initialize_tts(config)
        logger.bind(tag=TAG).info(f"Initialize component: tts success {select_tts_module}")

    # InitializeLLMModule
    if init_llm:
        select_llm_module = config["selected_module"]["LLM"]
        llm_type = (
            select_llm_module
            if "type" not in config["LLM"][select_llm_module]
            else config["LLM"][select_llm_module]["type"]
        )
        modules["llm"] = llm.create_instance(
            llm_type,
            config["LLM"][select_llm_module],
        )
        logger.bind(tag=TAG).info(f"Initialize component: llm success {select_llm_module}")

    # InitializeIntentModule
    if init_intent:
        select_intent_module = config["selected_module"]["Intent"]
        intent_type = (
            select_intent_module
            if "type" not in config["Intent"][select_intent_module]
            else config["Intent"][select_intent_module]["type"]
        )
        modules["intent"] = intent.create_instance(
            intent_type,
            config["Intent"][select_intent_module],
        )
        logger.bind(tag=TAG).info(f"Init component: intent success {select_intent_module}")

    # InitializeMemoryModule
    if init_memory:
        select_memory_module = config["selected_module"]["Memory"]
        memory_type = (
            select_memory_module
            if "type" not in config["Memory"][select_memory_module]
            else config["Memory"][select_memory_module]["type"]
        )
        modules["memory"] = memory.create_instance(
            memory_type,
            config["Memory"][select_memory_module],
            config.get("summaryMemory", None),
        )
        logger.bind(tag=TAG).info(f"Init component: memory success {select_memory_module}")

    # InitializeVADModule
    if init_vad:
        select_vad_module = config["selected_module"]["VAD"]
        vad_type = (
            select_vad_module
            if "type" not in config["VAD"][select_vad_module]
            else config["VAD"][select_vad_module]["type"]
        )
        modules["vad"] = vad.create_instance(
            vad_type,
            config["VAD"][select_vad_module],
        )
        logger.bind(tag=TAG).info(f"Initialize component: vad success {select_vad_module}")

    # Initialize ASRModule
    if init_asr:
        select_asr_module = config["selected_module"]["ASR"]
        modules["asr"] = initialize_asr(config)
        logger.bind(tag=TAG).info(f"Initialize component: asr success {select_asr_module}")
    return modules


def _selected_module(config, module_name):
    selected = config.get("selected_module", {}) or {}
    module_config = config.get(module_name, {}) or {}
    configured = selected.get(module_name)
    if configured in module_config:
        return configured
    if module_name == "TTS" and "EdgeTTS" in module_config:
        return "EdgeTTS"
    if len(module_config) == 1:
        return next(iter(module_config))
    return configured

def initialize_tts(config):
    select_tts_module = _selected_module(config, "TTS")
    if not select_tts_module or select_tts_module not in (config.get("TTS") or {}):
        raise KeyError("TTS")
    tts_type = (
        select_tts_module
        if "type" not in config["TTS"][select_tts_module]
        else config["TTS"][select_tts_module]["type"]
    )
    new_tts = tts.create_instance(
        tts_type,
        config["TTS"][select_tts_module],
        str(config.get("delete_audio", True)).lower() in ("true", "1", "yes"),
    )
    return new_tts


def initialize_asr(config):
    select_asr_module = config["selected_module"]["ASR"]
    asr_type = (
        select_asr_module
        if "type" not in config["ASR"][select_asr_module]
        else config["ASR"][select_asr_module]["type"]
    )
    new_asr = asr.create_instance(
        asr_type,
        config["ASR"][select_asr_module],
        str(config.get("delete_audio", True)).lower() in ("true", "1", "yes"),
    )
    logger.bind(tag=TAG).info("ASR module initialization completed")
    return new_asr


def initialize_voiceprint(asr_instance, config):
    """Initialize voiceprint recognition"""
    voiceprint_config = config.get("voiceprint")
    if not voiceprint_config:
        return False  

    # App Config
    if not voiceprint_config.get("url") or not voiceprint_config.get("speakers"):
        logger.bind(tag=TAG).warning("Voiceprint recognition config incomplete")
        return False
        
    try:
        asr_instance.init_voiceprint(voiceprint_config)
        logger.bind(tag=TAG).info("ASR voiceprint recognition dynamically enabled")
        logger.bind(tag=TAG).info(f"Configure speakerQuantity: {len(voiceprint_config['speakers'])}")
        return True
    except Exception as e:
        logger.bind(tag=TAG).error(f"DynamicInitialize voiceprint recognitionFail: {str(e)}")
        return False
