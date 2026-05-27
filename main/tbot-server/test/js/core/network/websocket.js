// WebSocketmessageProcessModule
import { getConfig, saveConnectionUrls } from '../../config/manager.js?v=0205';
import { uiController } from '../../ui/controller.js?v=0205';
import { log } from '../../utils/logger.js?v=0205';
import { getAudioPlayer } from '../audio/player.js?v=0205';
import { getAudioRecorder } from '../audio/recorder.js?v=0205';
import { executeMcpTool, getMcpTools, setWebSocket as setMcpWebSocket } from '../mcp/tools.js?v=0205';
import { webSocketConnect } from './ota-connector.js?v=0205';

// WebSocketHandlerclass
export class WebSocketHandler {
    constructor() {
        this.websocket = null;
        this.onConnectionStateChange = null;
        this.onRecordButtonStateChange = null;
        this.onSessionStateChange = null;
        this.onSessionEmotionChange = null;
        this.onChatMessage = null; // Add new: chat messageCallback
        this.currentSessionId = null;
        this.isRemoteSpeaking = false;
    }

    // Sendhellohandshake message
    async sendHelloMessage() {
        if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) return false;

        try {
            const config = getConfig();

            const helloMessage = {
                type: 'hello',
                device_id: config.deviceId,
                device_name: config.deviceName,
                device_mac: config.deviceMac,
                token: config.token,
                features: {
                    mcp: true
                }
            };

            log('Sendhellohandshake message', 'info');
            this.websocket.send(JSON.stringify(helloMessage));

            return new Promise(resolve => {
                const timeout = setTimeout(() => {
                    log('WaithelloResponsetimeout', 'error');
                    log('Prompt: please tryClick"TestAuth"ButtonPerformConnecttroubleshoot', 'info');
                    resolve(false);
                }, 5000);

                const onMessageHandler = (event) => {
                    try {
                        const response = JSON.parse(event.data);
                        if (response.type === 'hello' && response.session_id) {
                            log(`ServerhandshakeSuccess,SessionID: ${response.session_id}`, 'success');
                            clearTimeout(timeout);
                            this.websocket.removeEventListener('message', onMessageHandler);
                            resolve(true);
                        }
                    } catch (e) {
                        // IgnorenonJSONmessage
                    }
                };

                this.websocket.addEventListener('message', onMessageHandler);
            });
        } catch (error) {
            log(`Sendhellomessage error: ${error.message}`, 'error');
            return false;
        }
    }

    // Process Textmessage
    handleTextMessage(message) {
        if (message.type === 'hello') {
            log(`Serverresponse:${JSON.stringify(message, null, 2)}`, 'success');
            window.cameraAvailable = true;
            log('ConnectSuccess, camera available', 'success');
            uiController.updateDialButton(true);
            uiController.startAIChatSession();
        } else if (message.type === 'tts') {
            this.handleTTSMessage(message);
        } else if (message.type === 'audio') {
            log(`ReceiveAudio controlmessage: ${JSON.stringify(message)}`, 'info');
        } else if (message.type === 'stt') {
            log(`RecognizeResult: ${message.text}`, 'info');
            // CheckNeedBind Device
            if (message.text && (message.text.includes('Bind') || message.text.includes('bind'))) {
                log('receiveTo deviceBindPrompt,UpdatecameraStatus', 'warning');
                window.cameraAvailable = false;
                // Closecamera
                if (typeof window.stopCamera === 'function') {
                    window.stopCamera();
                }
                // UpdatecameraButtonStatus
                const cameraBtn = document.getElementById('cameraBtn');
                if (cameraBtn) {
                    cameraBtn.classList.remove('camera-active');
                    cameraBtn.querySelector('.btn-text').textContent = 'camera';
                    cameraBtn.disabled = true;
                    cameraBtn.title = 'please firstBindVerification code';
                }
            }
            // Usenew chat messageCallbackShowSTTmessage
            if (this.onChatMessage && message.text) {
                this.onChatMessage(message.text, true);
            }
        } else if (message.type === 'llm') {
            log(`largeModelreply: ${message.text}`, 'info');
            // Usenew chat messageCallbackShowLLMreply
            if (this.onChatMessage && message.text) {
                this.onChatMessage(message.text, false);
            }

            // IfcontainsEmoji,UpdatesessionStatusEmojiandTriggerLive2DAction
            if (message.text && /[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/u.test(message.text)) {
                // ExtractEmojisymbol
                const emojiMatch = message.text.match(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/u);
                if (emojiMatch && this.onSessionEmotionChange) {
                    this.onSessionEmotionChange(emojiMatch[0]);
                }

                // TriggerLive2DemotionAction
                if (message.emotion) {
                    console.log(`Receiveemotion message: emotion=${message.emotion}, text=${message.text}`);
                    this.triggerLive2DEmotionAction(message.emotion);
                }
            }

            // Only whenTextnot onlyEmojiwhen, only thenAdd toConversationmid
            // RemoveTextInEmojiafterCheck WhetheralsoContent
            const textWithoutEmoji = message.text ? message.text.replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]/gu, '').trim() : '';
            if (textWithoutEmoji && this.onChatMessage) {
                this.onChatMessage(message.text, false);
            }
        } else if (message.type === 'mcp') {
            this.handleMCPMessage(message);
        } else {
            log(`UnknownMessage Type: ${message.type}`, 'info');
            if (this.onChatMessage) {
                this.onChatMessage(`UnknownMessage Type: ${message.type}\n${JSON.stringify(message, null, 2)}`, false);
            }
        }
    }

    // ProcessTTSmessage
    handleTTSMessage(message) {
        if (message.state === 'start') {
            log('ServerStartSendVoice', 'info');
            this.currentSessionId = message.session_id;
            this.isRemoteSpeaking = true;
            if (this.onSessionStateChange) {
                this.onSessionStateChange(true);
            }

            // StartLive2Dspeaking animation
            this.startLive2DTalking();
        } else if (message.state === 'sentence_start') {
            log(`ServerSendVoicesegment: ${message.text}`, 'info');
            this.ttsSentenceCount = (this.ttsSentenceCount || 0) + 1;

            if (message.text && this.onChatMessage) {
                this.onChatMessage(message.text, false);
            }

            // Ensureanimation in sentenceStartwhenRun
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager && !live2dManager.isTalking) {
                this.startLive2DTalking();
            }
        } else if (message.state === 'sentence_end') {
            log(`VoicesegmentEnd: ${message.text}`, 'info');

            // sentenceEndwhen not clear animation,Waitnext sentence or finalStop
        } else if (message.state === 'stop') {
            log('ServerVoiceTransferEnd,Clear AllAudiobuffer', 'info');

            // Clear AllAudiobuffer andStopPlay
            const audioPlayer = getAudioPlayer();
            audioPlayer.clearAllAudio();

            this.isRemoteSpeaking = false;
            if (this.onRecordButtonStateChange) {
                this.onRecordButtonStateChange(false);
            }
            if (this.onSessionStateChange) {
                this.onSessionStateChange(false);
            }

            // DelayStopLive2Dspeaking animation, ensureall sentencesPlayfinished
            setTimeout(() => {
                this.stopLive2DTalking();
                this.ttsSentenceCount = 0; // Resetcounter
            }, 1000); // 1secondsDelay, ensureall sentencesComplete
        }
    }

    // StartLive2Dspeaking animation
    startLive2DTalking() {
        try {
            // GetLive2DManagedeviceInstance
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager && live2dManager.live2dModel) {
                // UseAudioPlaydevice's analyzer node
                live2dManager.startTalking();
                log('Live2Dspeaking animation alreadyStart', 'info');
            }
        } catch (error) {
            log(`StartLive2Dspeaking animationFail: ${error.message}`, 'error');
        }
    }

    // StopLive2Dspeaking animation
    stopLive2DTalking() {
        try {
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager) {
                live2dManager.stopTalking();
                log('Live2Dspeaking animation alreadyStop', 'info');
            }
        } catch (error) {
            log(`StopLive2Dspeaking animationFail: ${error.message}`, 'error');
        }
    }

    // InitializeLive2DAudioanalyzer
    initializeLive2DAudioAnalyzer() {
        try {
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager) {
                // InitializeAudioanalyzer (UseAudioPlaydevice'sContext)
                if (live2dManager.initializeAudioAnalyzer()) {
                    log('Live2DAudioanalyzerInitialization complete,ConnectedtoAudioPlaydevice', 'success');
                } else {
                    log('Live2DAudioanalyzerInitializeFail, willUseSimulateanimation', 'warning');
                }
            }
        } catch (error) {
            log(`InitializeLive2DAudioanalyzerFail: ${error.message}`, 'error');
        }
    }

    // ProcessMCPmessage
    handleMCPMessage(message) {
        const payload = message.payload || {};
        log(`ServerIssue: ${JSON.stringify(message)}`, 'info');

        if (payload.method === 'tools/list') {
            const tools = getMcpTools();

            const replyMessage = JSON.stringify({
                "session_id": message.session_id || "",
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": payload.id,
                    "result": {
                        "tools": tools
                    }
                }
            });
            log(`ClientReport: ${replyMessage}`, 'info');
            this.websocket.send(replyMessage);
            log(`replyMCPTool list: ${tools.length} countTool`, 'info');

        } else if (payload.method === 'tools/call') {
            const toolName = payload.params?.name;
            const toolArgs = payload.params?.arguments;

            log(`CallTool: ${toolName} Parameter: ${JSON.stringify(toolArgs)}`, 'info');

            executeMcpTool(toolName, toolArgs).then(result => {
                const replyMessage = JSON.stringify({
                    "session_id": message.session_id || "",
                    "type": "mcp",
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": payload.id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": JSON.stringify(result)
                                }
                            ],
                            "isError": false
                        }
                    }
                });

                log(`ClientReport: ${replyMessage}`, 'info');
                this.websocket.send(replyMessage);
            }).catch(error => {
                log(`ToolExecuteFail: ${error.message}`, 'error');
                const errorReply = JSON.stringify({
                    "session_id": message.session_id || "",
                    "type": "mcp",
                    "payload": {
                        "jsonrpc": "2.0",
                        "id": payload.id,
                        "error": {
                            "code": -32603,
                            "message": error.message
                        }
                    }
                });
                this.websocket.send(errorReply);
            });
        } else if (payload.method === 'initialize') {
            log(`ReceiveToolInitializeRequest: ${JSON.stringify(payload.params)}`, 'info');
            // Savevision analysisAPI address
            const visionUrl = document.getElementById('visionUrl');
            const visionConfig = payload?.params?.capabilities?.vision;
            if (visionConfig && typeof visionConfig === 'object' && visionConfig.url && visionConfig.token) {
                const visionConfigStr = JSON.stringify(visionConfig);
                localStorage.setItem('xz_tester_vision', visionConfigStr);
                if (visionUrl) visionUrl.value = visionConfig.url;
            } else {
                localStorage.removeItem('xz_tester_vision');
                if (visionUrl) visionUrl.value = '';
            }

            const replyMessage = JSON.stringify({
                "session_id": message.session_id || "",
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "id": payload.id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {}
                        },
                        "serverInfo": {
                            "name": "tbot-web-test",
                            "version": "2.1.0"
                        }
                    }
                }
            });
            log(`replyInitializeResponse`, 'info');
            this.websocket.send(replyMessage);
        } else {
            log(`UnknownofMCPMethod: ${payload.method}`, 'warning');
        }
    }

    // Handle binarymessage
    async handleBinaryMessage(data) {
        try {
            let arrayBuffer;
            if (data instanceof ArrayBuffer) {
                arrayBuffer = data;
            } else if (data instanceof Blob) {
                arrayBuffer = await data.arrayBuffer();
                log(`ReceiveBlobAudio Data,Size: ${arrayBuffer.byteLength}Byte`, 'debug');
            } else {
                log(`ReceiveUnknownTypeofBinaryData: ${typeof data}`, 'warning');
                return;
            }

            const opusData = new Uint8Array(arrayBuffer);
            const audioPlayer = getAudioPlayer();
            audioPlayer.enqueueAudioData(opusData);
        } catch (error) {
            log(`Handle binarymessage error: ${error.message}`, 'error');
        }
    }

    // ConnectWebSocketServer
    async connect() {
        const config = getConfig();
        log('runningCheckOTAStatus...', 'info');
        saveConnectionUrls();

        try {
            const otaUrl = document.getElementById('otaUrl').value.trim();
            const ws = await webSocketConnect(otaUrl, config);
            if (ws === undefined) {
                return false;
            }
            this.websocket = ws;

            // SetreceiveBinaryDataofType isArrayBuffer
            this.websocket.binaryType = 'arraybuffer';

            // Set MCP Moduleof WebSocket Instance
            setMcpWebSocket(this.websocket);

            // Setrecorder'sWebSocket
            const audioRecorder = getAudioRecorder();
            audioRecorder.setWebSocket(this.websocket);

            this.setupEventHandlers();

            return true;
        } catch (error) {
            log(`Connecterror: ${error.message}`, 'error');
            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(false);
            }
            return false;
        }
    }

    // SeteventHandler
    setupEventHandlers() {
        this.websocket.onopen = async () => {
            const url = document.getElementById('serverUrl').value;
            log(`ConnectedtoServer: ${url}`, 'success');

            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(true);
            }

            // ConnectSuccessafter,DefaultStatusas listening
            this.isRemoteSpeaking = false;
            if (this.onSessionStateChange) {
                this.onSessionStateChange(false);
            }

            // inWebSocketConnectSuccesswhenInitializeLive2DAudioanalyzer
            this.initializeLive2DAudioAnalyzer();

            await this.sendHelloMessage();
        };

        this.websocket.onclose = () => {
            log('disconnectedConnect', 'info');

            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(false);
            }

            const audioRecorder = getAudioRecorder();
            audioRecorder.stop();

            // Closecamera
            if (typeof window.stopCamera === 'function') {
                window.stopCamera();
            }

            // HidecameraShowArea
            const cameraContainer = document.getElementById('cameraContainer');
            if (cameraContainer) {
                cameraContainer.classList.remove('active');
            }
        };

        this.websocket.onerror = (error) => {
            log(`WebSocketerror: ${error.message || 'Unknownerror'}`, 'error');
            uiController.addChatMessage(`⚠️ WebSocketerror: ${error.message || 'Unknownerror'}`, false);
            if (this.onConnectionStateChange) {
                this.onConnectionStateChange(false);
            }
        };

        this.websocket.onmessage = (event) => {
            try {
                if (typeof event.data === 'string') {
                    const message = JSON.parse(event.data);
                    this.handleTextMessage(message);
                } else {
                    this.handleBinaryMessage(event.data);
                }
            } catch (error) {
                log(`WebSocketmessageProcesserror: ${error.message}`, 'error');
                // no longerUseoldaddMessageFunction,BecauseconversationDivelementDoes not exist
                // error message willPassOtherModeShow
            }
        };
    }

    // disconnectConnect
    disconnect() {
        if (!this.websocket) return;

        this.websocket.close();
        const audioRecorder = getAudioRecorder();
        audioRecorder.stop();

        // Closecamera
        if (typeof window.stopCamera === 'function') {
            window.stopCamera();
        }

        // HidecameraShowArea
        const cameraContainer = document.getElementById('cameraContainer');
        if (cameraContainer) {
            cameraContainer.classList.remove('active');
        }
    }

    // Send Textmessage
    sendTextMessage(text) {
        if (text === '' || !this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
            return false;
        }

        try {
            // Ifother side speaking, firstSendinterrupt message
            if (this.isRemoteSpeaking && this.currentSessionId) {
                const abortMessage = {
                    session_id: this.currentSessionId,
                    type: 'abort',
                    reason: 'wake_word_detected'
                };
                this.websocket.send(JSON.stringify(abortMessage));
                log('Sendinterrupt message', 'info');
            }

            const listenMessage = {
                type: 'listen',
                state: 'detect',
                text: text
            };

            this.websocket.send(JSON.stringify(listenMessage));
            log(`Send Textmessage: ${text}`, 'info');

            return true;
        } catch (error) {
            log(`Sendmessage error: ${error.message}`, 'error');
            return false;
        }
    }

    /**
     * TriggerLive2DemotionAction
     * @param {string} emotion - emotionName
     */
    triggerLive2DEmotionAction(emotion) {
        try {
            const live2dManager = window.chatApp?.live2dManager;
            if (live2dManager && typeof live2dManager.triggerEmotionAction === 'function') {
                live2dManager.triggerEmotionAction(emotion);
                log(`TriggerLive2DemotionAction: ${emotion}`, 'info');
            } else {
                log(`unableTriggerLive2DemotionAction: Live2DManagedeviceNot foundorMethodunavailable`, 'warning');
            }
        } catch (error) {
            log(`TriggerLive2DemotionActionFail: ${error.message}`, 'error');
        }
    }

    // GetWebSocketInstance
    getWebSocket() {
        return this.websocket;
    }

    // Check WhetherConnected
    isConnected() {
        return this.websocket && this.websocket.readyState === WebSocket.OPEN;
    }
}

// Createsingleton
let wsHandlerInstance = null;

export function getWebSocketHandler() {
    if (!wsHandlerInstance) {
        wsHandlerInstance = new WebSocketHandler();
    }
    return wsHandlerInstance;
}
