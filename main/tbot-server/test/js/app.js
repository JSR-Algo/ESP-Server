// main app entry
import { checkOpusLoaded, initOpusEncoder } from './core/audio/opus-codec.js?v=0205';
import { getAudioPlayer } from './core/audio/player.js?v=0205';
import { checkMicrophoneAvailability, isHttpNonLocalhost } from './core/audio/recorder.js?v=0205';
import { initMcpTools } from './core/mcp/tools.js?v=0205';
import { uiController } from './ui/controller.js?v=0205';
import { log } from './utils/logger.js?v=0205';

// helperFunction: willBase64Convert data toBlob
function dataURItoBlob(dataURI) {
    const byteString = atob(dataURI.split(',')[1]);
    const mimeString = dataURI.split(',')[0].split(':')[1].split(';')[0];
    const ab = new ArrayBuffer(byteString.length);
    const ia = new Uint8Array(ab);
    for (let i = 0; i < byteString.length; i++) {
        ia[i] = byteString.charCodeAt(i);
    }
    return new Blob([ab], { type: mimeString });
}

// app class
class App {
    constructor() {
        this.uiController = null;
        this.audioPlayer = null;
        this.live2dManager = null;
        this.cameraStream = null;
        this.currentFacingMode = 'user';
    }

    // Initializeapp
    async init() {
        log('runningInitializeapp...', 'info');
        // InitializeUIController
        this.uiController = uiController;
        this.uiController.init();
        // CheckOpuslibrary
        checkOpusLoaded();
        // InitializeOpusEncoder
        initOpusEncoder();
        // InitializeAudioPlaydevice
        this.audioPlayer = getAudioPlayer();
        await this.audioPlayer.start();
        // InitializeMCPTool
        initMcpTools();
        // Checkmicrophone availability
        await this.checkMicrophoneAvailability();
        // Checkcamera availability
        this.checkCameraAvailability();
        // InitializeLive2D
        await this.initLive2D();
        // Initializecamera
        this.initCamera();
        // CloseLoadloading
        this.setModelLoadingStatus(false);
        log('appInitialization complete', 'success');
    }

    // InitializeLive2D
    async initLive2D() {
        try {
            // CheckLive2DManagerAlreadyLoad
            if (typeof window.Live2DManager === 'undefined') {
                throw new Error('Live2DManagernot yetLoad, pleaseCheckScriptImportorder');
            }
            this.live2dManager = new window.Live2DManager();
            await this.live2dManager.initializeLive2D();
            // UpdateUIStatus
            const live2dStatus = document.getElementById('live2dStatus');
            if (live2dStatus) {
                live2dStatus.textContent = '● Loaded';
                live2dStatus.className = 'status loaded';
            }
            log('Live2DInitialization complete', 'success');
        } catch (error) {
            log(`Live2DInitializeFail: ${error.message}`, 'error');
            // UpdateUIStatus
            const live2dStatus = document.getElementById('live2dStatus');
            if (live2dStatus) {
                live2dStatus.textContent = '● LoadFail';
                live2dStatus.className = 'status error';
            }
        }
    }

    // SetmodelLoadStatus
    setModelLoadingStatus(isLoading) {
        const modelLoading = document.getElementById('modelLoading');
        if (modelLoading) {
            modelLoading.style.display = isLoading ? 'flex' : 'none';
        }
    }

    /**
     * Checkmicrophone availability
     * in appInitializeCall when,CheckmicrophoneAvailableandUpdateUIStatus
     */
    async checkMicrophoneAvailability() {
        try {
            const isAvailable = await checkMicrophoneAvailability();
            const isHttp = isHttpNonLocalhost();
            // SaveavailabilityStatusto globalVariable
            window.microphoneAvailable = isAvailable;
            window.isHttpNonLocalhost = isHttp;
            // UpdateUI
            if (this.uiController) {
                this.uiController.updateMicrophoneAvailability(isAvailable, isHttp);
            }
            log(`microphone availabilityCheckComplete: ${isAvailable ? 'available' : 'unavailable'}`, isAvailable ? 'success' : 'warning');
        } catch (error) {
            log(`Checkmicrophone availabilityFail: ${error.message}`, 'error');
            // DefaultSetas unavailable
            window.microphoneAvailable = false;
            window.isHttpNonLocalhost = isHttpNonLocalhost();
            if (this.uiController) {
                this.uiController.updateMicrophoneAvailability(false, window.isHttpNonLocalhost);
            }
        }
    }

    // Checkcamera availability
    checkCameraAvailability() {
        window.cameraAvailable = true;
        log('camera availabilityCheckComplete: DefaultalreadyBindVerification code', 'success');
    }

    // Initializecamera
    async initCamera() {
        const cameraContainer = document.getElementById('cameraContainer');
        const cameraVideo = document.getElementById('cameraVideo');
        const cameraSwitch = document.getElementById('cameraSwitch');
        const cameraSwitchMask = document.getElementById('cameraSwitchMask');
        const dialBtn = document.getElementById('dialBtn');

        if (!cameraContainer || !cameraVideo) {
            log('camera elementNot found, skipInitialize', 'warning');
            return Promise.resolve(false);
        }

        let isDragging = false;
        let currentX, currentY, initialX, initialY;
        let xOffset = 0, yOffset = 0;

        cameraContainer.addEventListener('mousedown', dragStart);
        document.addEventListener('mousemove', drag);
        document.addEventListener('mouseup', dragEnd);
        cameraContainer.addEventListener('touchstart', dragStart, { passive: false });
        document.addEventListener('touchmove', drag, { passive: false });
        document.addEventListener('touchend', dragEnd);

        function dragStart(e) {
            if (e.type === 'touchstart') {
                initialX = e.touches[0].clientX - xOffset;
                initialY = e.touches[0].clientY - yOffset;
            } else {
                initialX = e.clientX - xOffset;
                initialY = e.clientY - yOffset;
            }
            isDragging = true;
            cameraContainer.classList.add('dragging');
        }

        function drag(e) {
            if (isDragging) {
                e.preventDefault();
                if (e.type === 'touchmove') {
                    currentX = e.touches[0].clientX - initialX;
                    currentY = e.touches[0].clientY - initialY;
                } else {
                    currentX = e.clientX - initialX;
                    currentY = e.clientY - initialY;
                }
                xOffset = currentX;
                yOffset = currentY;
                cameraContainer.style.transform = `translate3d(${currentX}px, ${currentY}px, 0)`;
            }
        }

        function dragEnd() {
            initialX = currentX;
            initialY = currentY;
            isDragging = false;
            cameraContainer.classList.remove('dragging');
        }

        return new Promise((resolve) => {
            window.startCamera = async () => {
                try {
                    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                        log('BrowsernotSupportcameraAPI', 'warning');
                        return false;
                    }
                    log('runningRequestcamera permission...', 'info');
                    this.cameraStream = await navigator.mediaDevices.getUserMedia({
                        video: { width: 180, height: 240, facingMode: this.currentFacingMode },
                        audio: false
                    });
                    cameraVideo.srcObject = this.cameraStream;
                    const devices = await navigator.mediaDevices.enumerateDevices();
                    const videoDevices = devices.filter(device => device.kind === 'videoinput');
                    if (videoDevices.length > 1) {
                        if (cameraSwitch) cameraSwitch.classList.add('active'); 
                    }
                    cameraContainer.classList.add('active');

                    // Switchhang up whenCase
                    const hasActive = dialBtn.classList.contains('dial-active');
                    if (!hasActive) {
                        cameraContainer.classList.remove('active');
                        cameraSwitch.classList.remove('active');
                        window.stopCamera();
                    }
                    log('camera alreadyStart', 'success');
                    return true;
                } catch (error) {
                    log(`StartcameraFail: ${error.name} - ${error.message}`, 'error');
                    if (error.name === 'NotAllowedError') {
                        log('camera permission denied, pleaseCheckBrowserSet', 'warning');
                    } else if (error.name === 'NotFoundError') {
                        log('Not foundcameraDevice', 'warning');
                    } else if (error.name === 'NotReadableError') {
                        log('camera alreadyOtheroccupied by program', 'warning');
                    }
                    return false;
                }
            };

            window.stopCamera = () => {
                if (this.cameraStream) {
                    this.cameraStream.getTracks().forEach(track => track.stop());
                    this.cameraStream = null;
                    cameraVideo.srcObject = null;
                    log('camera alreadyClose', 'info');
                }
            };

            window.switchCamera = async() => {
                if (window.switchCameraTimer) return;
                if (this.cameraStream) {
                    const currentTransform = window.getComputedStyle(cameraContainer).transform;
                    const originalTransform = currentTransform === 'none' ? 'translate(0px, 0px)' : currentTransform;
                    cameraContainer.style.setProperty('--original-transform', originalTransform);
                    cameraContainer.classList.add('flip');
                    if (cameraSwitchMask) cameraSwitchMask.style.opacity = 0; 
                    this.currentFacingMode = this.currentFacingMode === 'user' ? 'environment' : 'user';
                    window.stopCamera();
                    window.startCamera();
                    
                    window.switchCameraTimer = setTimeout(() => {
                        if (this.currentFacingMode === 'user') {
                            cameraVideo.style.transform = 'scaleX(-1)';
                        } else {
                            cameraVideo.style.transform = 'scaleX(1)';
                        }
                        window.switchCameraTimer = null;
                        cameraContainer.classList.remove('flip');
                        cameraContainer.style.removeProperty('--original-transform');
                        if (cameraSwitchMask) cameraSwitchMask.style.opacity = 1; 
                    }, 500);
                }
            };

            window.takePhoto = (question = 'Descriptionitem seen') => {
                return new Promise(async (resolve) => {
                    const canvas = document.createElement('canvas');
                    const video = cameraVideo;

                    if (!video || video.readyState !== video.HAVE_ENOUGH_DATA) {
                        log('cannot take photo: camera not ready', 'warning');
                        resolve({
                            success: false,
                            error: 'camera not ready, pleaseEnsureConnectedand camera alreadyStart'
                        });
                        return;
                    }

                    canvas.width = video.videoWidth || 180;
                    canvas.height = video.videoHeight || 240;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

                    const photoData = canvas.toDataURL('image/jpeg', 0.8);
                    log(`take photoSuccess, imageData Length: ${photoData.length}`, 'success');

                    try {
                        const xz_tester_vision = localStorage.getItem('xz_tester_vision');
                        if (xz_tester_vision) {
                            let visionInfo = null;

                            try {
                                visionInfo = JSON.parse(xz_tester_vision);
                            } catch (err) {
                                throw new Error(`visionConfigParse Failed`);
                            }

                            const { url, token } = visionInfo || {};
                            if (!url || !token) {
                                throw new Error('vision analysisFail:ConfigMissingAPI address(url)or token(token)');
                            }

                            log(`runningSendImageto vision analysisInterface: ${url}`, 'info');

                            const deviceId = document.getElementById('deviceMac')?.value || '';
                            const clientId = document.getElementById('clientId')?.value || 'web_test_client';

                            const formData = new FormData();
                            formData.append('question', question);
                            formData.append('image', dataURItoBlob(photoData), 'photo.jpg');

                            const response = await fetch(url, {
                                method: 'POST',
                                body: formData,
                                headers: {
                                    'Device-Id': deviceId,
                                    'Client-Id': clientId,
                                    'Authorization': `Bearer ${token}`
                                }
                            });

                            if (!response.ok) {
                                throw new Error(`HTTP error! status: ${response.status}`);
                            }

                            const analysisResult = await response.json();
                            log(`vision analysisComplete: ${JSON.stringify(analysisResult).substring(0, 200)}...`, 'success');

                            resolve({
                                success: true,
                                message: question,
                                photo_data: photoData,
                                photo_width: canvas.width,
                                photo_height: canvas.height,
                                vision_analysis: analysisResult
                            });
                        } else {
                            log('not yetConfigvision analysisService', 'warning');
                        }
                    } catch (error) {
                        log(`vision analysisFail: ${error.message}`, 'error');
                        resolve({
                            success: true,
                            message: question,
                            photo_data: photoData,
                            photo_width: canvas.width,
                            photo_height: canvas.height,
                            vision_analysis: {
                                success: false,
                                error: error.message,
                                fallback: 'Cannot connect tovision analysisService'
                            }
                        });
                    }
                });
            };

            log('cameraInitialization complete', 'success');
            resolve(true);
        });
    }
}

// Create andStartapp
const app = new App();
// expose appInstanceto global, forOtherModuleAccess
window.chatApp = app;
document.addEventListener('DOMContentLoaded', () => {
    // Initializeapp
    app.init();
});
export default app;
