import asyncio
import time
import aiohttp
import requests
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict
from config.logger import setup_logging
from core.utils.cache.manager import cache_manager
from core.utils.cache.config import CacheType

TAG = __name__
logger = setup_logging()


class VoiceprintProvider:
    """Voiceprint recognition service provider"""
    
    def __init__(self, config: dict):
        self.original_url = config.get("url", "")
        self.speakers = config.get("speakers", [])
        self.speaker_map = self._parse_speakers()
        # Voiceprint recognition similarity threshold, default0.4
        self.similarity_threshold = float(config.get("similarity_threshold", 0.4))
        
        # ParseAPIAddress andKey
        self.api_url = None
        self.api_key = None
        self.speaker_ids = []
        
        if not self.original_url:
            logger.bind(tag=TAG).warning("Voiceprint recognition URL not configured; voiceprint recognition disabled")
            self.enabled = False
        else:
            # ParseURLandkey
            parsed_url = urlparse(self.original_url)
            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
            
            # Extract from query paramskey
            query_params = parse_qs(parsed_url.query)
            self.api_key = query_params.get('key', [''])[0]
            
            if not self.api_key:
                logger.bind(tag=TAG).error("key parameter not found in URL; voiceprint recognition disabled")
                self.enabled = False
            else:
                # ConstructidentifyAPI address
                self.api_url = f"{base_url}/voiceprint/identify"
                
                # Extractspeaker_ids
                for speaker_str in self.speakers:
                    try:
                        parts = speaker_str.split(",", 2)
                        if len(parts) >= 1:
                            speaker_id = parts[0].strip()
                            self.speaker_ids.append(speaker_id)
                    except Exception:
                        continue
                
                # Check whether valid speaker config exists
                if not self.speaker_ids:
                    logger.bind(tag=TAG).warning("No valid speaker configured; voiceprint recognition disabled")
                    self.enabled = False
                else:
                    # Perform health check, verify server available
                    if self._check_server_health():
                        self.enabled = True
                        logger.bind(tag=TAG).info(f"Voiceprint recognition enabled: API={self.api_url}, speakers={len(self.speaker_ids)}, similarity threshold={self.similarity_threshold}")
                    else:
                        self.enabled = False
                        logger.bind(tag=TAG).warning(f"Voiceprint recognition server unavailable, voiceprint recognition disabled: {self.api_url}")
    
    def _parse_speakers(self) -> Dict[str, Dict[str, str]]:
        """Parse speaker config"""
        speaker_map = {}
        for speaker_str in self.speakers:
            try:
                parts = speaker_str.split(",", 2)
                if len(parts) >= 3:
                    speaker_id, name, description = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    speaker_map[speaker_id] = {
                        "name": name,
                        "description": description
                    }
            except Exception as e:
                logger.bind(tag=TAG).warning(f"Failed to parse speaker config: {speaker_str}, error: {e}")
        return speaker_map
    
    def _check_server_health(self) -> bool:
        """Check voiceprint recognition server health status"""
        if not self.api_url or not self.api_key:
            return False
    
        cache_key = f"{self.api_url}:{self.api_key}"
        
        # Check Cache
        cached_result = cache_manager.get(CacheType.VOICEPRINT_HEALTH, cache_key)
        if cached_result is not None:
            logger.bind(tag=TAG).debug(f"Use cached health status: {cached_result}")
            return cached_result
        
        # Cache expired or missing
        logger.bind(tag=TAG).info("Perform voiceprint server health check")
        
        try:
            # Health CheckURL
            parsed_url = urlparse(self.api_url)
            health_url = f"{parsed_url.scheme}://{parsed_url.netloc}/voiceprint/health?key={self.api_key}"
            
            # Send health check request
            response = requests.get(health_url, timeout=3)
            
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "healthy":
                    logger.bind(tag=TAG).info("Voiceprint recognition server health check passed")
                    is_healthy = True
                else:
                    logger.bind(tag=TAG).warning(f"Voiceprint recognition server status abnormal: {result}")
                    is_healthy = False
            else:
                logger.bind(tag=TAG).warning(f"Voiceprint recognition server health check failed: HTTP {response.status_code}")
                is_healthy = False
                
        except requests.exceptions.ConnectTimeout:
            logger.bind(tag=TAG).warning("Voiceprint recognition server connection timeout")
            is_healthy = False
        except requests.exceptions.ConnectionError:
            logger.bind(tag=TAG).warning("Voiceprint recognition server connection refused")
            is_healthy = False
        except Exception as e:
            logger.bind(tag=TAG).warning(f"Voiceprint recognition server health check exception: {e}")
            is_healthy = False
        
        # UseGlobal cache managerCache Result
        cache_manager.set(CacheType.VOICEPRINT_HEALTH, cache_key, is_healthy)
        logger.bind(tag=TAG).info(f"Health check result cached: {is_healthy}")
        
        return is_healthy
    
    async def identify_speaker(self, audio_data: bytes, session_id: str) -> Optional[str]:
        """Identify speaker"""
        if not self.enabled or not self.api_url or not self.api_key:
            logger.bind(tag=TAG).debug("Voiceprint recognition disabled or not configured, skip recognition")
            return None
            
        try:
            api_start_time = time.monotonic()
            
            # PrepareRequest header
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Accept': 'application/json'
            }
            
            # Preparemultipart/form-dataData
            data = aiohttp.FormData()
            data.add_field('speaker_ids', ','.join(self.speaker_ids))
            data.add_field('file', audio_data, filename='audio.wav', content_type='audio/wav')
            
            timeout = aiohttp.ClientTimeout(total=10)
            
            # Network Request
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.api_url, headers=headers, data=data) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        speaker_id = result.get("speaker_id")
                        score = result.get("score", 0)
                        total_elapsed_time = time.monotonic() - api_start_time
                        
                        logger.bind(tag=TAG).info(f"Voiceprint recognition time: {total_elapsed_time:.3f}s")
                        
                        # Similarity threshold check
                        if score < self.similarity_threshold:
                            logger.bind(tag=TAG).warning(f"Voiceprint recognition similarity {score:.3f} below threshold {self.similarity_threshold}")
                            return "Unknown speaker"
                        
                        if speaker_id and speaker_id in self.speaker_map:
                            result_name = self.speaker_map[speaker_id]["name"]
                            logger.bind(tag=TAG).info(f"Voiceprint recognition successful: {result_name} (similarity: {score:.3f})")
                            return result_name
                        else:
                            logger.bind(tag=TAG).warning(f"Unrecognized speaker ID: {speaker_id}")
                            return "Unknown speaker"
                    else:
                        logger.bind(tag=TAG).error(f"Voiceprint recognition API error: HTTP {response.status}")
                        return None
                        
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - api_start_time
            logger.bind(tag=TAG).error(f"Voiceprint recognition timeout: {elapsed:.3f}s")
            return None
        except Exception as e:
            elapsed = time.monotonic() - api_start_time
            logger.bind(tag=TAG).error(f"Voiceprint recognition failed: {e}")
            return None

