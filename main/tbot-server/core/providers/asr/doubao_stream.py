import json
import gzip
import uuid
import asyncio
import websockets
import opuslib_next
from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.text = ""
        self.decoder = opuslib_next.Decoder(16000, 1)
        self.asr_ws = None
        self.forward_task = None
        self.is_processing = False  # Add ProcessingStatusFlag
        self._is_stopping = False  # Add stop flag, prevent race condition

        # Config Parameters
        self.appid = str(config.get("appid"))
        self.access_token = config.get("access_token")
        # ResourceID, used to distinguish differentASRmodel (default1.0model hourly version,v2Version Useseed-asr)
        self.resource_id = config.get("resource_id", "volc.bigasr.sauc.duration")

        self.boosting_table_name = config.get("boosting_table_name", "")
        self.correct_table_name = config.get("correct_table_name", "")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file

        # VolcengineASRConfig
        enable_multilingual = config.get("enable_multilingual", False)
        self.enable_multilingual = (
            False if str(enable_multilingual).lower() == "false" else True
        )
        if self.enable_multilingual:
            self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
        else:
            self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
        self.uid = config.get("uid", "streaming_asr_service")
        self.workflow = config.get(
            "workflow", "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate"
        )
        self.result_type = config.get("result_type", "single")
        self.format = config.get("format", "pcm")
        self.codec = config.get("codec", "pcm")
        self.rate = config.get("sample_rate", 16000)
        # languageParameter only in multilingual mode(bigmodel_nostream)Valid under
        self.language = config.get("language") if self.enable_multilingual else None
        self.bits = config.get("bits", 16)
        self.channel = config.get("channel", 1)
        self.auth_method = config.get("auth_method", "token")
        self.secret = config.get("secret", "access_secret")
        end_window_size = config.get("end_window_size")
        self.end_window_size = int(end_window_size) if end_window_size else 200

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        # Call parent class method first for base logic
        await super().receive_audio(conn, audio, audio_have_voice)
        
        # If this time hasVoiceand connection not established before
        if audio_have_voice and self.asr_ws is None and not self.is_processing:
            try:
                self.is_processing = True
                # Create NewWebSocketConnect
                headers = self.token_auth() if self.auth_method == "token" else None
                logger.bind(tag=TAG).info(f"Connecting to ASR service, headers: {headers}")

                self.asr_ws = await websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    max_size=1000000000,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                )

                # Send init request
                request_params = self.construct_request(str(uuid.uuid4()))
                try:
                    payload_bytes = str.encode(json.dumps(request_params))
                    payload_bytes = gzip.compress(payload_bytes)
                    full_client_request = self.generate_header()
                    full_client_request.extend((len(payload_bytes)).to_bytes(4, "big"))
                    full_client_request.extend(payload_bytes)

                    logger.bind(tag=TAG).info(f"Send initialization request: {request_params}")
                    await self.asr_ws.send(full_client_request)

                    # Wait initializationResponse
                    init_res = await self.asr_ws.recv()
                    result = self.parse_response(init_res)
                    logger.bind(tag=TAG).info(f"Received initialization response: {result}")

                    # Check initializationResponse
                    if "code" in result and result["code"] != 1000:
                        error_msg = f"ASR serviceInitialization failed: {result.get('payload_msg', {}).get('error', 'Unknown error')}"
                        logger.bind(tag=TAG).error(error_msg)
                        raise Exception(error_msg)

                except Exception as e:
                    logger.bind(tag=TAG).error(f"Send initializationRequest failed: {str(e)}")
                    if hasattr(e, "__cause__") and e.__cause__:
                        logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
                    raise e

                # Start ReceiveASRAsync task for result
                self.forward_task = asyncio.create_task(self._forward_asr_results(conn))

                # Send cached audio data
                if conn.asr_audio and len(conn.asr_audio) > 0:
                    for cached_audio in conn.asr_audio[-10:]:
                        try:
                            pcm_frame = self.decoder.decode(cached_audio, 960)
                            payload = gzip.compress(pcm_frame)
                            audio_request = bytearray(
                                self.generate_audio_default_header()
                            )
                            audio_request.extend(len(payload).to_bytes(4, "big"))
                            audio_request.extend(payload)
                            await self.asr_ws.send(audio_request)
                        except Exception as e:
                            logger.bind(tag=TAG).info(
                                f"Error sending cached audio data: {e}"
                            )

            except Exception as e:
                logger.bind(tag=TAG).error(f"Failed to establish ASR connection: {str(e)}")
                if hasattr(e, "__cause__") and e.__cause__:
                    logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
                if self.asr_ws:
                    await self.asr_ws.close()
                    self.asr_ws = None
                self.is_processing = False
                return

        # Send current audio data
        if self.asr_ws and self.is_processing and not self._is_stopping:
            try:
                pcm_frame = self.decoder.decode(audio, 960)
                payload = gzip.compress(pcm_frame)
                audio_request = bytearray(self.generate_audio_default_header())
                audio_request.extend(len(payload).to_bytes(4, "big"))
                audio_request.extend(payload)
                await self.asr_ws.send(audio_request)
            except Exception as e:
                logger.bind(tag=TAG).info(f"Error occurred while sending audio data: {e}")

    async def _forward_asr_results(self, conn: "ConnectionHandler"):
        try:
            while self.asr_ws and not conn.stop_event.is_set():
                # Get audio data of current connection
                audio_data = conn.asr_audio
                try:
                    response = await self.asr_ws.recv()
                    result = self.parse_response(response)
                    logger.bind(tag=TAG).debug(f"Received ASR result: {result}")

                    if "payload_msg" in result:
                        payload = result["payload_msg"]
                        # Check whether it is error code 1013 (no valid speech)
                        if "code" in payload and payload["code"] == 1013:
                            # Silent handling,Do not recordErrorLog
                            continue

                        if "result" in payload:
                            utterances = payload["result"].get("utterances", [])
                            # CheckdurationAnd empty text cases
                            if (
                                not self.enable_multilingual  # Note: multilingual mode does not return intermediate results. Need wait for final result
                                and payload.get("audio_info", {}).get("duration", 0)
                                > 2000
                                and not utterances
                                and not payload["result"].get("text")
                                and conn.client_listen_mode != "manual"
                            ):
                                logger.bind(tag=TAG).error(f"Recognized text: empty")
                                self.text = ""
                                if len(audio_data) > 15:  # Ensure enough audio data
                                    await self.handle_voice_stop(conn, audio_data)
                                break

                            # Specially handle recognition results without text (in manual mode recognition may be complete but button not released)
                            elif not payload["result"].get("text") and not utterances:
                                # Multilingual mode keeps returning empty text until final complete result, so need exclude
                                if self.enable_multilingual:
                                    continue

                                if conn.client_listen_mode == "manual" and conn.client_voice_stop and len(audio_data) > 15:
                                    logger.bind(tag=TAG).debug("MessageEnd received stop signal, trigger handling")
                                    await self.handle_voice_stop(conn, audio_data)
                                    break

                            for utterance in utterances:
                                if utterance.get("definite", False):
                                    current_text = utterance["text"]
                                    logger.bind(tag=TAG).info(
                                        f"Text recognized: {current_text}"
                                    )

                                    # Accumulate recognition results in manual mode
                                    if conn.client_listen_mode == "manual":
                                        if self.text:
                                            self.text += current_text
                                        else:
                                            self.text = current_text

                                        # When receivingMessageReceived stop signal midway
                                        if conn.client_voice_stop and len(audio_data) > 0:
                                            logger.bind(tag=TAG).debug("MessageReceived stop signal midway, trigger handling")
                                            await self.handle_voice_stop(conn, audio_data)
                                        break
                                    else:
                                        # Direct overwrite in auto mode
                                        self.text = current_text
                                        if len(audio_data) > 15:  # Ensure enough audio data
                                            await self.handle_voice_stop(
                                                conn, audio_data
                                            )
                                    break
                        elif "error" in payload:
                            error_msg = payload.get("error", "Unknown error")
                            logger.bind(tag=TAG).error(f"ASR serviceReturnError: {error_msg}")
                            break

                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).info("ASR service connection closed")
                    self.is_processing = False
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(f"ProcessASRError occurred when resultError: {str(e)}")
                    if hasattr(e, "__cause__") and e.__cause__:
                        logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
                    self.is_processing = False
                    break

        except Exception as e:
            logger.bind(tag=TAG).error(f"ASRResult forwarding task occurredError: {str(e)}")
            if hasattr(e, "__cause__") and e.__cause__:
                logger.bind(tag=TAG).error(f"Error cause: {str(e.__cause__)}")
        finally:
            if self.asr_ws:
                await self.asr_ws.close()
                self.asr_ws = None
            self.is_processing = False
            self._is_stopping = False
            # Reset all audio-relatedStatus
            conn.reset_audio_states()

    def stop_ws_connection(self):
        if self.asr_ws:
            asyncio.create_task(self.asr_ws.close())
            self.asr_ws = None
        self.is_processing = False
        self._is_stopping = False

    async def _send_stop_request(self):
        """Send last audio frame to notify server of end"""
        self._is_stopping = True  # Mark as stopping first to prevent later audio sending
        if self.asr_ws:
            try:
                # Send audio frame with end marker (gzip-compressed empty data)
                empty_payload = gzip.compress(b"")
                last_audio_request = bytearray(
                    self.generate_last_audio_default_header()
                )
                last_audio_request.extend(len(empty_payload).to_bytes(4, "big"))
                last_audio_request.extend(empty_payload)
                await self.asr_ws.send(last_audio_request)
                logger.bind(tag=TAG).debug("Sent end audio frame")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"Error sending end audio frame: {e}")

    def construct_request(self, reqid):
        req = {
            "app": {
                "appid": self.appid,
                "token": self.access_token,
            },
            "user": {"uid": self.uid},
            "request": {
                "reqid": reqid,
                "workflow": self.workflow,
                "show_utterances": True,
                "result_type": self.result_type,
                "sequence": 1,
                "end_window_size": self.end_window_size,
                "corpus": {
                    "boosting_table_name": self.boosting_table_name,
                    "correct_table_name": self.correct_table_name,
                }
            },
            "audio": {
                "format": self.format,
                "codec": self.codec,
                "rate": self.rate,
                "bits": self.bits,
                "channel": self.channel,
                "sample_rate": self.rate,
            },
        }

        # languageParams only added in multilingual mode
        if self.enable_multilingual and self.language:
            req["audio"]["language"] = self.language

        logger.bind(tag=TAG).debug(
            f"Construct request parameters: {json.dumps(req, ensure_ascii=False)}"
        )
        return req

    def token_auth(self):
        return {
            "X-Api-App-Key": self.appid,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

    def generate_header(
        self,
        version=0x01,
        message_type=0x01,
        message_type_specific_flags=0x00,
        serial_method=0x01,
        compression_type=0x01,
        reserved_data=0x00,
        extension_header: bytes = b"",
    ):
        header = bytearray()
        header_size = int(len(extension_header) / 4) + 1
        header.append((version << 4) | header_size)
        header.append((message_type << 4) | message_type_specific_flags)
        header.append((serial_method << 4) | compression_type)
        header.append(reserved_data)
        header.extend(extension_header)
        return header

    def generate_audio_default_header(self):
        return self.generate_header(
            version=0x01,
            message_type=0x02,
            message_type_specific_flags=0x00,
            serial_method=0x01,
            compression_type=0x01,
        )

    def generate_last_audio_default_header(self):
        return self.generate_header(
            version=0x01,
            message_type=0x02,
            message_type_specific_flags=0x02,
            serial_method=0x01,
            compression_type=0x01,
        )

    def parse_response(self, res: bytes) -> dict:
        try:
            # CheckResponseLength
            if len(res) < 4:
                logger.bind(tag=TAG).error(f"Response dataInsufficient Length: {len(res)}")
                return {"error": "Response dataInsufficient Length"}

            # GetMessagehead
            header = res[:4]
            message_type = header[1] >> 4

            # If isErrorResponse
            if message_type == 0x0F:  # SERVER_ERROR_RESPONSE
                code = int.from_bytes(res[4:8], "big", signed=False)
                msg_length = int.from_bytes(res[8:12], "big", signed=False)
                error_msg = json.loads(res[12:].decode("utf-8"))
                return {
                    "code": code,
                    "msg_length": msg_length,
                    "payload_msg": error_msg,
                }

            # GetJSONData
            try:
                # Check Byte8-11Whether validJSONLength Field
                # Format:4Byte header + 4Byte sequence number + 4Byte Length + JSONData
                length = int.from_bytes(res[8:12], "big")
                if length > 0 and length <= len(res) - 12:
                    # Length field exists, read JSON of specified length from byte 12
                    json_data = res[12:12 + length].decode("utf-8")
                else:
                    # No length field or invalid length, try direct parsing
                    json_data = res[8:].decode("utf-8")
                result = json.loads(json_data)
                logger.bind(tag=TAG).debug(f"Parsed SuccessfullyJSONResponse: {result}")
                return {"payload_msg": result}
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.bind(tag=TAG).error(f"JSONParse Failed: {str(e)}")
                logger.bind(tag=TAG).error(f"Raw Data: {res}")
                raise

        except Exception as e:
            logger.bind(tag=TAG).error(f"ParseResponseFail: {str(e)}")
            logger.bind(tag=TAG).error(f"OriginalResponse data: {res.hex()}")
            raise

    async def speech_to_text(self, opus_data, session_id, audio_format, artifacts=None):
        result = self.text
        self.text = ""  # Cleartext
        return result, None

    async def close(self):
        """Resource cleanup method"""
        if self.asr_ws:
            await self.asr_ws.close()
            self.asr_ws = None
        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass
            self.forward_task = None
        self.is_processing = False

        # Explicit ReleasedecoderResource
        if hasattr(self, "decoder") and self.decoder is not None:
            try:
                del self.decoder
                self.decoder = None
                logger.bind(tag=TAG).debug("Doubao decoder resources released")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"ReleaseDoubao decoderError with resource: {e}")
