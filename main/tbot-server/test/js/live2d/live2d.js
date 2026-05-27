/**
 * Live2D Managedevice
 * responsible for Live2D Model'sInitialize, mouth animationControletcFunction
 */
class Live2DManager {
    constructor() {
        this.live2dApp = null;
        this.live2dModel = null;
        this.isTalking = false;
        this.mouthAnimationId = null;
        this.mouthParam = 'ParamMouthOpenY';
        this.audioContext = null;
        this.analyser = null;
        this.dataArray = null;
        this.lastEmotionActionTime = null;
        this.currentModelName = null;

        // ModelspecificConfig
        this.modelConfig = {
            'hiyori_pro_zh': {
                mouthParam: 'ParamMouthOpenY',
                mouthAmplitude: 1.0,
                mouthThresholds: { low: 0.3, high: 0.7 },
                motionMap: {
                    'FlickUp': 'FlickUp',
                    'FlickDown': 'FlickDown',
                    'Tap': 'Tap',
                    'Tap@Body': 'Tap@Body',
                    'Flick': 'Flick',
                    'Flick@Body': 'Flick@Body'
                }
            },
            'natori_pro_zh': {
                mouthParam: 'ParamMouthOpenY',
                mouthAmplitude: 1.0,
                mouthThresholds: { low: 0.1, high: 0.4 },
                mouthFormParam: 'ParamMouthForm',
                mouthFormAmplitude: 1.0,
                mouthForm2Param: 'ParamMouthForm2',
                mouthForm2Amplitude: 0.8,
                motionMap: {
                    'FlickUp': 'FlickUp',
                    'FlickDown': 'Flick@Body',
                    'Tap': 'Tap',
                    'Tap@Body': 'Tap@Head',
                    'Flick': 'Tap',
                    'Flick@Body': 'Flick@Body'
                }
            }
        };

        // emotion toActionMapping of
        this.emotionToActionMap = {
            'happy': 'FlickUp',      // happy-swipe upAction
            'laughing': 'FlickUp',   // laugh-swipe upAction
            'funny': 'FlickUp',      // funny-swipe upAction
            'sad': 'FlickDown',      // sad-swipe downAction
            'crying': 'FlickDown',   // cry-swipe downAction
            'angry': 'Tap@Body',     // angry-bodyClickAction
            'surprised': 'Tap',      // surprised-ClickAction
            'neutral': 'Flick',      // normal-swipeAction
            'default': 'Flick@Body'  // Default-body swipeAction
        };

        // single/double-click detectionConfigandStatus
        this._lastClickTime = 0;
        this._lastClickPos = { x: 0, y: 0 };
        this._singleClickTimer = null;
        this._doubleClickMs = 280; // double-clickTimethreshold(ms)
        this._doubleClickDist = 16; // double-click allowedMaximumdisplacement(px)
        // swipe detection
        this._pointerDown = false;
        this._downPos = { x: 0, y: 0 };
        this._downTime = 0;
        this._downArea = 'Body';
        this._movedBeyondClick = false;
        this._swipeMinDist = 24; // Triggerswipe'sMinimumdistance
    }

    /**
     * Initialize Live2D
     */
    async initializeLive2D() {
        try {
            const canvas = document.getElementById('live2d-stage');

            // for internalUse
            window.PIXI = PIXI;

            this.live2dApp = new PIXI.Application({
                view: canvas,
                height: window.innerHeight,
                width: window.innerWidth,
                resolution: window.devicePixelRatio,
                autoDensity: true,
                antialias: true,
                backgroundAlpha: 0,
            });

            // Load Live2D Model - DynamicDetectCurrentDirectoryadapt to different environments
            // Get currentHTMLFilewhere locatedDirectoryPath
            const currentPath = window.location.pathname;
            const lastSlashIndex = currentPath.lastIndexOf('/');
            const basePath = currentPath.substring(0, lastSlashIndex + 1);

            // from localStorage ReadlastSelectedModel,If noneThen useDefault
            const savedModelName = localStorage.getItem('live2dModel') || 'hiyori_pro_zh';
            const modelFileMap = {
                'hiyori_pro_zh': 'hiyori_pro_t11.model3.json',
                'natori_pro_zh': 'natori_pro_t06.model3.json'
            };
            const modelFileName = modelFileMap[savedModelName] || 'hiyori_pro_t11.model3.json';
            const modelPath = basePath + 'resources/' + savedModelName + '/runtime/' + modelFileName;

            this.live2dModel = await PIXI.live2d.Live2DModel.from(modelPath);
            this.live2dApp.stage.addChild(this.live2dModel);

            // SaveCurrentModel name
            this.currentModelName = savedModelName;

            // UpdatedropdownShow
            const modelSelect = document.getElementById('live2dModelSelect');
            if (modelSelect) {
                modelSelect.value = savedModelName;
            }

            // SetModelspecific mouthParametername
            if (this.modelConfig[savedModelName]) {
                this.mouthParam = this.modelConfig[savedModelName].mouthParam || 'ParamMouthOpenY';
            }

            // SetModelProperty
            this.live2dModel.scale.set(0.33);
            this.live2dModel.x = (window.innerWidth - this.live2dModel.width) * 0.5;
            this.live2dModel.y = -50;

            // Enableinteract andListenClickhit (Header/body etc.)

            this.live2dModel.interactive = true;


            this.live2dModel.on('doublehit', (args) => {
                const area = Array.isArray(args) ? args[0] : args;

                // Triggerdouble-clickAction
                if (area === 'Body') {
                    this.motion('Flick@Body');
                } else if (area === 'Head' || area === 'Face') {
                    this.motion('Flick');
                }

                const app = window.chatApp;
                const payload = JSON.stringify({ type: 'live2d', event: 'doublehit', area });
                if (app && app.dataChannel && app.dataChannel.readyState === 'open') {
                    app.dataChannel.send(payload);
                }

            });

            this.live2dModel.on('singlehit', (args) => {
                const area = Array.isArray(args) ? args[0] : args;

                // Triggersingle-clickAction
                if (area === 'Body') {
                    this.motion('Tap@Body');
                } else if (area === 'Head' || area === 'Face') {
                    this.motion('Tap');
                }

                const app = window.chatApp;
                const payload = JSON.stringify({ type: 'live2d', event: 'singlehit', area });
                if (app && app.dataChannel && app.dataChannel.readyState === 'open') {
                    app.dataChannel.send(payload);
                }

            });

            this.live2dModel.on('swipe', (args) => {
                const area = Array.isArray(args) ? args[0] : args;
                const dir = Array.isArray(args) ? args[1] : undefined;

                // TriggerswipeAction
                if (area === 'Body') {
                    if (dir === 'up') {
                        this.motion('FlickUp');
                    } else if (dir === 'down') {
                        this.motion('FlickDown');
                    }
                } else if (area === 'Head' || area === 'Face') {
                    if (dir === 'up') {
                        this.motion('FlickUp');
                    } else if (dir === 'down') {
                        this.motion('FlickDown');
                    }
                }

                const app = window.chatApp;
                const payload = JSON.stringify({ type: 'live2d', event: 'swipe', area, dir });
                if (app && app.dataChannel && app.dataChannel.readyState === 'open') {
                    app.dataChannel.send(payload);
                }

            });

            // fallback:Custom"Header/body"hitArea + single/double-click/swipeDistinguish
            this.live2dModel.on('pointerdown', (event) => {
                try {
                    const global = event.data.global;
                    const bounds = this.live2dModel.getBounds();
                    // Only atClickfalls inModelVisibleRangewithin determine
                    if (!bounds || !bounds.contains(global.x, global.y)) return;

                    const relX = (global.x - bounds.x) / (bounds.width || 1);
                    const relY = (global.y - bounds.y) / (bounds.height || 1);
                    let area = '';
                    // empirical threshold:ModelVisibleupper part of rectangle 20% Treat as"Header"Area
                    if (relX >= 0.4 && relX <= 0.6) {
                        if (relY <= 0.15) {
                            area = 'Head';
                        } else if (relY <= 0.23) {
                            area = 'Face';
                        } else {
                            area = 'Body';
                        }
                    }
                    if (area === '') {
                        return;
                    }

                    // Recordpress downStatusUsed forswipe detection
                    this._pointerDown = true;
                    this._downPos = { x: global.x, y: global.y };
                    this._downTime = performance.now();
                    this._downArea = area;
                    this._movedBeyondClick = false;

                    const now = performance.now();
                    const dt = now - (this._lastClickTime || 0);
                    const dx = global.x - (this._lastClickPos?.x || 0);
                    const dy = global.y - (this._lastClickPos?.y || 0);
                    const dist = Math.hypot(dx, dy);

                    // hitConfirm:Only whenClickinModeldo single on/double-clickDetermine
                    if (this._lastClickTime && dt <= this._doubleClickMs && dist <= this._doubleClickDist) {
                        // determined as double-click:CancelwaitTriggersingle-click event of
                        if (this._singleClickTimer) {
                            clearTimeout(this._singleClickTimer);
                            this._singleClickTimer = null;
                        }
                        if (typeof this.live2dModel.emit === 'function') {
                            this.live2dModel.emit('doublehit', [area]);
                        }
                        this._lastClickTime = 0;
                        this._pointerDown = false; // double-clickComplete,ResetStatus
                        return;
                    }

                    // may be single-click:RecordandDelayConfirm
                    this._lastClickTime = now;
                    this._lastClickPos = { x: global.x, y: global.y };
                    if (this._singleClickTimer) {
                        clearTimeout(this._singleClickTimer);
                        this._singleClickTimer = null;
                    }
                    this._singleClickTimer = setTimeout(() => {
                        // if inWaitmovement exceeded threshold during, no longer treated as single-click
                        if (!this._movedBeyondClick && typeof this.live2dModel.emit === 'function') {
                            this.live2dModel.emit('singlehit', [area]);
                        }
                        this._singleClickTimer = null;
                        this._lastClickTime = 0;
                    }, this._doubleClickMs);
                } catch (e) {
                    // IgnoreCustomhitDetermineInexception, avoid affecting main flow
                }
            });

            // pointer move:Used fordetermineWhetherfrom"Click"upgrade to"swipe"
            this.live2dModel.on('pointermove', (event) => {
                try {
                    if (!this._pointerDown) return;
                    const global = event.data.global;
                    const dx = global.x - this._downPos.x;
                    const dy = global.y - this._downPos.y;
                    const dist = Math.hypot(dx, dy);

                    // Use _doubleClickDist AsClick/swipe detection threshold
                    if (dist > this._doubleClickDist) {
                        this._movedBeyondClick = true;
                        // if already exceedsClickthreshold,Cancelpossible single-clickTrigger
                        if (this._singleClickTimer) {
                            clearTimeout(this._singleClickTimer);
                            this._singleClickTimer = null;
                        }
                        this._lastClickTime = 0;
                    }
                } catch (e) {
                    // Ignoremovement detectionInexception
                }
            });

            // pointer up:ConfirmWhetheras swipe
            const handlePointerUp = (event) => {
                try {
                    if (!this._pointerDown) return;
                    const global = (event && event.data && event.data.global) ? event.data.global : { x: this._downPos.x, y: this._downPos.y };
                    const dx = global.x - this._downPos.x;
                    const dy = global.y - this._downPos.y;
                    const dist = Math.hypot(dx, dy);

                    // swipe: exceed swipeMinimumdistance thenTrigger swipe event (Carrydirection andArea)
                    if (this._movedBeyondClick && dist >= this._swipeMinDist) {
                        if (typeof this.live2dModel.emit === 'function') {
                            const dir = Math.abs(dx) >= Math.abs(dy)
                                ? (dx > 0 ? 'right' : 'left')
                                : (dy > 0 ? 'down' : 'up');
                            this.live2dModel.emit('swipe', [this._downArea, dir]);
                        }
                        // terminate: no longer let single-click/double-clickTrigger
                        if (this._singleClickTimer) {
                            clearTimeout(this._singleClickTimer);
                            this._singleClickTimer = null;
                        }
                        this._lastClickTime = 0;
                    }
                } catch (e) {
                    // Ignorerelease determinationInexception
                }
                finally {
                    this._pointerDown = false;
                    this._movedBeyondClick = false;
                }
            };

            this.live2dModel.on('pointerup', handlePointerUp);
            this.live2dModel.on('pointerupoutside', handlePointerUp);

            // AddwindowSizeChangeListener, keepModelinCanvasmiddle andBottom
            window.addEventListener('resize', () => {
                if (this.live2dModel) {
                    // UsewindowActualsizeAgainCompute modtype position
                    this.live2dModel.x = (window.innerWidth - this.live2dModel.width) * 0.5;
                    this.live2dModel.y = -50;
                }
            });

        } catch (err) {
            console.error('Load Live2D ModelFail:', err);
        }
    }

    /**
     * InitializeAudioanalyzer - UseAudioPlaydevice's analyzer node
     */
    initializeAudioAnalyzer() {
        try {
            // Get audio playbackdeviceInstance
            const audioPlayer = window.chatApp?.audioPlayer;
            if (!audioPlayer) {
                console.warn('AudioPlaydevice not yetInitialize,Cannot Getanalyzer node');
                return false;
            }

            // Get audio playbackdevice'sAudio context
            this.audioContext = audioPlayer.getAudioContext();
            if (!this.audioContext) {
                console.warn('unableGet audio playbackdevice'sAudio context');
                return false;
            }

            // Createanalyzer node
            this.analyser = this.audioContext.createAnalyser();
            this.analyser.fftSize = 256;
            this.dataArray = new Uint8Array(this.analyser.frequencyBinCount);

            return true;
        } catch (error) {
            console.error('InitializeAudioanalyzerFail:', error);
            return false;
        }
    }

    /**
     * ConnecttoAudioPlaydevice'sOutputnode
     */
    connectToAudioPlayer() {
        try {
            // Get audio playbackdevice's streamContext
            const audioPlayer = window.chatApp?.audioPlayer;
            if (!audioPlayer || !audioPlayer.streamingContext) {
                console.warn('AudioPlaydevice or streamContextnot yetInitialize');
                return false;
            }

            // Get audio playbackdevice's streamContext
            const streamingContext = audioPlayer.streamingContext;

            // Getanalyzer node
            const analyser = streamingContext.getAnalyser();
            if (!analyser) {
                console.warn('AudioPlaydevice not yetCreateanalyzer node, unableConnect');
                return false;
            }

            // UseAudioPlaydevice's analyzer node
            this.analyser = analyser;
            this.dataArray = new Uint8Array(this.analyser.frequencyBinCount);
            return true;
        } catch (error) {
            console.error('ConnecttoAudioPlaydeviceFail:', error);
            return false;
        }
    }

    /**
     * mouth animationLoop
     */
    animateMouth() {
        if (!this.isTalking) return;
        if (!this.live2dModel) return;
        const internal = this.live2dModel && this.live2dModel.internalModel;
        if (internal && internal.coreModel) {
            const coreModel = internal.coreModel;

            let mouthOpenY = 0;
            let mouthForm = 0;
            let mouthForm2 = 0;
            let average = 0;

            if (this.analyser && this.dataArray) {
                this.analyser.getByteFrequencyData(this.dataArray);
                average = this.dataArray.reduce((a, b) => a + b) / this.dataArray.length;

                const normalizedVolume = average / 255;

                // GetModelspecific threshold
                let lowThreshold = 0.3;
                let highThreshold = 0.7;
                if (this.currentModelName && this.modelConfig[this.currentModelName]) {
                    lowThreshold = this.modelConfig[this.currentModelName].mouthThresholds?.low || 0.3;
                    highThreshold = this.modelConfig[this.currentModelName].mouthThresholds?.high || 0.7;
                }

                // UseModelspecific thresholdPerformMap
                let minOpenY = 0.1;
                if (this.currentModelName && this.modelConfig[this.currentModelName]) {
                    minOpenY = this.modelConfig[this.currentModelName].mouthMinOpenY || 0.1;
                }

                if (normalizedVolume < lowThreshold) {
                    mouthOpenY = minOpenY + Math.pow(normalizedVolume / lowThreshold, 1.5) * (0.4 - minOpenY);
                } else if (normalizedVolume < highThreshold) {
                    mouthOpenY = 0.4 + (normalizedVolume - lowThreshold) / (highThreshold - lowThreshold) * 0.4;
                } else {
                    mouthOpenY = 0.8 + Math.pow((normalizedVolume - highThreshold) / (1 - highThreshold), 1.2) * 0.2;
                }

                // appModelspecific mouth opening amplitude
                let amplitudeMultiplier = 1.0;
                let maxOpenY = 2.5;
                if (this.currentModelName && this.modelConfig[this.currentModelName]) {
                    amplitudeMultiplier = this.modelConfig[this.currentModelName].mouthAmplitude;
                    maxOpenY = this.modelConfig[this.currentModelName].maxOpenY || 2.5;
                }
                mouthOpenY = mouthOpenY * amplitudeMultiplier;
                mouthOpenY = Math.min(Math.max(mouthOpenY, 0), maxOpenY);

                // Calculatemouth shapeParameter(only forSupportmouth shapeChangeModel of)
                if (this.currentModelName && this.modelConfig[this.currentModelName]?.mouthFormParam) {
                    const config = this.modelConfig[this.currentModelName];
                    const formAmplitude = config.mouthFormAmplitude || 0.5;
                    const form2Amplitude = config.mouthForm2Amplitude || 0;

                    // lip sync followsVolumeChange:
                    // lowVolume: lip shape leans"one"word (negative value)
                    // highVolume: lip shape leans"o"word (positive value)
                    // Volume=0when: lip shape=0(naturalStatus)
                    mouthForm = (normalizedVolume - 0.5) * 2 * formAmplitude;
                    mouthForm = Math.max(-formAmplitude, Math.min(formAmplitude, mouthForm));

                    // second mouth shapeParameter(natorispecific)
                    if (config.mouthForm2Param) {
                        mouthForm2 = (normalizedVolume - 0.3) * 2 * form2Amplitude;
                        mouthForm2 = Math.max(-form2Amplitude, Math.min(form2Amplitude, mouthForm2));
                    }
                }

                // Debug Log:OutputmouthParameter
                console.log(`[Live2D] Model: ${this.currentModelName || 'unknown'}, Volume: ${average?.toFixed(0)}, OpenY: ${mouthOpenY.toFixed(3)}, Form: ${mouthForm.toFixed(3)}, Form2: ${mouthForm2.toFixed(3)}`);
            }

            // Setmouth opening/closingParameter
            coreModel.setParameterValueById(this.mouthParam, mouthOpenY);

            // Setmouth shapeParameter(only forSupportmouth shapeChangeModel of)
            if (this.currentModelName && this.modelConfig[this.currentModelName]?.mouthFormParam) {
                const config = this.modelConfig[this.currentModelName];
                const formParam = config.mouthFormParam;
                coreModel.setParameterValueById(formParam, mouthForm);

                // Setsecond mouth shapeParameter(natorispecific)
                if (config.mouthForm2Param) {
                    coreModel.setParameterValueById(config.mouthForm2Param, mouthForm2);
                }
            }

            coreModel.update();
        }
        this.mouthAnimationId = requestAnimationFrame(() => this.animateMouth());
    }

    /**
     * Startspeaking animation
     */
    startTalking() {
        if (this.isTalking || !this.live2dModel) return;

        // EnsureAudioanalyzerInitialized
        if (!this.analyser) {
            if (!this.initializeAudioAnalyzer()) {
                console.warn('AudioanalyzerInitializeFail, willUseSimulateanimation');
                // even if analyzerInitializeFail, alsoStartanimation (UseSimulateData)
                this.isTalking = true;
                this.animateMouth();
                return;
            }
        }

        // ConnecttoAudioPlaydeviceOutput
        if (!this.connectToAudioPlayer()) {
            console.warn('Cannot connect toAudioPlaydeviceOutput, willUseSimulateanimation');
        }

        this.isTalking = true;
        this.animateMouth();
    }

    /**
     * Stopspeaking animation
     */
    stopTalking() {
        this.isTalking = false;
        if (this.mouthAnimationId) {
            cancelAnimationFrame(this.mouthAnimationId);
            this.mouthAnimationId = null;
        }

        // ResetmouthParameter
        if (this.live2dModel) {
            const internal = this.live2dModel.internalModel;
            if (internal && internal.coreModel) {
                const coreModel = internal.coreModel;
                coreModel.setParameterValueById(this.mouthParam, 0);
                coreModel.update();
            }
        }
    }

    /**
     * Based onemotionTriggerAction
     * @param {string} emotion - emotionName
     */
    triggerEmotionAction(emotion) {
        if (!this.live2dModel) return;

        // AddcooldownTimeControl, avoid too frequentTrigger
        const now = Date.now();
        if (this.lastEmotionActionTime && now - this.lastEmotionActionTime < 5000) { // 5seconds cooldownTime
            return;
        }

        // Based onemotionGet correspondingAction
        const action = this.emotionToActionMap[emotion] || this.emotionToActionMap['default'];

        // TriggerActionandRecordTime
        this.motion(action);
        this.lastEmotionActionTime = now;
    }



    /**
     * TriggerModelAction(Motion)
     * @param {string} name - ActionGroupName, such as 'TapBody','FlickUp','Idle' etc
     */
    motion(name) {
        try {
            if (!this.live2dModel) return;

            // By currentModelGet correspondingActionName
            let actualMotionName = name;
            if (this.currentModelName && this.modelConfig[this.currentModelName]) {
                const motionMap = this.modelConfig[this.currentModelName].motionMap;
                actualMotionName = motionMap[name] || name;
            }

            this.live2dModel.motion(actualMotionName);
        } catch (error) {
            console.error('TriggerActionFail:', error);
        }
    }

    /**
     * SetModelinteraction event
     */
    setupModelInteractions() {
        if (!this.live2dModel) return;

        this.live2dModel.interactive = true;

        this.live2dModel.on('doublehit', (args) => {
            const area = Array.isArray(args) ? args[0] : args;

            if (area === 'Body') {
                this.motion('Flick@Body');
            } else if (area === 'Head' || area === 'Face') {
                this.motion('Flick');
            }

            const app = window.chatApp;
            const payload = JSON.stringify({ type: 'live2d', event: 'doublehit', area });
            if (app && app.dataChannel && app.dataChannel.readyState === 'open') {
                app.dataChannel.send(payload);
            }
        });

        this.live2dModel.on('singlehit', (args) => {
            const area = Array.isArray(args) ? args[0] : args;

            if (area === 'Body') {
                this.motion('Tap@Body');
            } else if (area === 'Head' || area === 'Face') {
                this.motion('Tap');
            }

            const app = window.chatApp;
            const payload = JSON.stringify({ type: 'live2d', event: 'singlehit', area });
            if (app && app.dataChannel && app.dataChannel.readyState === 'open') {
                app.dataChannel.send(payload);
            }
        });

        this.live2dModel.on('swipe', (args) => {
            const area = Array.isArray(args) ? args[0] : args;
            const dir = Array.isArray(args) ? args[1] : undefined;

            if (area === 'Body') {
                if (dir === 'up') {
                    this.motion('FlickUp');
                } else if (dir === 'down') {
                    this.motion('FlickDown');
                }
            }

            const app = window.chatApp;
            const payload = JSON.stringify({ type: 'live2d', event: 'swipe', area, dir });
            if (app && app.dataChannel && app.dataChannel.readyState === 'open') {
                app.dataChannel.send(payload);
            }
        });

        this.live2dModel.on('pointerdown', (event) => {
            try {
                const global = event.data.global;
                const bounds = this.live2dModel.getBounds();
                if (!bounds || !bounds.contains(global.x, global.y)) return;

                const relX = (global.x - bounds.x) / (bounds.width || 1);
                const relY = (global.y - bounds.y) / (bounds.height || 1);
                let area = '';

                if (relX >= 0.4 && relX <= 0.6) {
                    if (relY <= 0.15) {
                        area = 'Head';
                    } else if (relY >= 0.7) {
                        area = 'Body';
                    }
                }

                if (!area) return;

                const now = Date.now();
                const dt = now - (this._lastClickTime || 0);
                const dx = global.x - (this._lastClickPos?.x || 0);
                const dy = global.y - (this._lastClickPos?.y || 0);
                const dist = Math.hypot(dx, dy);

                if (this._lastClickTime && dt <= this._doubleClickMs && dist <= this._doubleClickDist) {
                    if (this._singleClickTimer) {
                        clearTimeout(this._singleClickTimer);
                        this._singleClickTimer = null;
                    }

                    this.live2dModel.emit('doublehit', area);
                    this._lastClickTime = null;
                    this._lastClickPos = null;
                } else {
                    this._lastClickTime = now;
                    this._lastClickPos = { x: global.x, y: global.y };

                    this._singleClickTimer = setTimeout(() => {
                        this._singleClickTimer = null;
                        this.live2dModel.emit('singlehit', area);
                    }, this._doubleClickMs);
                }
            } catch (e) {
                console.warn('pointerdown Processerror occurred:', e);
            }
        });
    }

    /**
     * CleanResource
     */
    destroy() {
        this.stopTalking();

        // CleanAudioanalyzer
        if (this.audioContext) {
            this.audioContext.close();
            this.audioContext = null;
        }
        this.analyser = null;
        this.dataArray = null;

        // Clean Live2D app
        if (this.live2dApp) {
            this.live2dApp.destroy(true);
            this.live2dApp = null;
        }
        this.live2dModel = null;
    }

    /**
     * Switch Live2D Model
     * @param {string} modelName - ModelDirectoryName, such as 'hiyori_pro_zh','natori_pro_zh'
     * @returns {Promise<boolean>} - SwitchSuccessful
     */
    async switchModel(modelName) {
        try {
            // GetModelFilenameMap
            const modelFileMap = {
                'hiyori_pro_zh': 'hiyori_pro_t11.model3.json',
                'natori_pro_zh': 'natori_pro_t06.model3.json',
                'chitose': 'chitose.model3.json',
                'haru_greeter_pro_jp': 'haru_greeter_t05.model3.json'
            };

            const modelFileName = modelFileMap[modelName];
            if (!modelFileName) {
                console.error('UnknownofModel name:', modelName);
                return false;
            }

            // GetBase Path
            const currentPath = window.location.pathname;
            const lastSlashIndex = currentPath.lastIndexOf('/');
            const basePath = currentPath.substring(0, lastSlashIndex + 1);
            const modelPath = basePath + 'resources/' + modelName + '/runtime/' + modelFileName;

            // IfAlready existsModel, firstRemove
            if (this.live2dModel) {
                this.live2dApp.stage.removeChild(this.live2dModel);
                this.live2dModel.destroy();
                this.live2dModel = null;
            }

            // ShowLoadStatus
            const app = window.chatApp;
            if (app) {
                app.setModelLoadingStatus(true);
            }

            // LoadnewModel
            this.live2dModel = await PIXI.live2d.Live2DModel.from(modelPath);
            this.live2dApp.stage.addChild(this.live2dModel);

            // SetModelProperty
            this.live2dModel.scale.set(0.33);
            this.live2dModel.x = (window.innerWidth - this.live2dModel.width) * 0.5;
            this.live2dModel.y = -50;

            // AgainBindinteraction event
            this.setupModelInteractions();

            // HideLoadStatus
            if (app) {
                app.setModelLoadingStatus(false);
            }

            // SaveCurrentModel name
            this.currentModelName = modelName;

            // SetModelspecific mouthParametername
            if (this.modelConfig[modelName]) {
                this.mouthParam = this.modelConfig[modelName].mouthParam || 'ParamMouthOpenY';
            }

            // Saveto localStorage
            localStorage.setItem('live2dModel', modelName);

            // UpdatedropdownShow
            const modelSelect = document.getElementById('live2dModelSelect');
            if (modelSelect) {
                modelSelect.value = modelName;
            }

            console.log('ModelSwitchSuccess:', modelName);
            return true;
        } catch (error) {
            console.error('SwitchModelFail:', error);
            const app = window.chatApp;
            if (app) {
                app.setModelLoadingStatus(false);
            }
            return false;
        }
    }


}

// export globalInstance
window.Live2DManager = Live2DManager;
