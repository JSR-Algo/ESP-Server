import { log } from '../../utils/logger.js?v=0205';


// CheckOpuslibraryAlreadyLoad
export function checkOpusLoaded() {
    try {
        // CheckModuleExists(Localglobal exported by libraryVariable)
        if (typeof Module === 'undefined') {
            throw new Error('Opuslibrary notLoad,ModuleObjectDoes not exist');
        }

        // try firstUseModule.instance(libopus.jsLastone-line exportMode)
        if (typeof Module.instance !== 'undefined' && typeof Module.instance._opus_decoder_get_size === 'function') {
            // UseModule.instanceObjectreplace globalModuleObject
            window.ModuleInstance = Module.instance;
            log('OpuslibraryLoadSuccess(UseModule.instance)', 'success');

            // 3After secondsHideStatus
            const statusElement = document.getElementById('scriptStatus');
            if (statusElement) statusElement.style.display = 'none';
            return;
        }

        // If noneModule.instance,CheckglobalModuleFunction
        if (typeof Module._opus_decoder_get_size === 'function') {
            window.ModuleInstance = Module;
            log('OpuslibraryLoadSuccess(UseglobalModule)', 'success');

            // 3After secondsHideStatus
            const statusElement = document.getElementById('scriptStatus');
            if (statusElement) statusElement.style.display = 'none';
            return;
        }

        throw new Error('OpusDecodeFunctionNot found, mayModulestructure incorrect');
    } catch (err) {
        log(`OpuslibraryLoadFail, pleaseChecklibopus.jsFileExistsand correct: ${err.message}`, 'error');
    }
}


// Create oneOpusEncoder
let opusEncoder = null;
export function initOpusEncoder() {
    try {
        if (opusEncoder) {
            return opusEncoder; // alreadyInitializepassed
        }

        if (!window.ModuleInstance) {
            log('unableCreateOpusEncoder:ModuleInstanceunavailable', 'error');
            return;
        }

        // InitializeoneOpusEncoder
        const mod = window.ModuleInstance;
        const sampleRate = 16000; // 16kHzSample rate
        const channels = 1;       // Mono
        const application = 2048; // OPUS_APPLICATION_VOIP = 2048

        // CreateEncoder
        opusEncoder = {
            channels: channels,
            sampleRate: sampleRate,
            frameSize: 960, // 60ms @ 16kHz = 60 * 16 = 960 samples
            maxPacketSize: 4000, // MaximumpacketSize
            module: mod,

            // InitializeEncoder
            init: function () {
                try {
                    // GetEncoderSize
                    const encoderSize = mod._opus_encoder_get_size(this.channels);
                    log(`OpusEncoderSize: ${encoderSize}Byte`, 'info');

                    // allocate memory
                    this.encoderPtr = mod._malloc(encoderSize);
                    if (!this.encoderPtr) {
                        throw new Error("unable allocateEncodermemory");
                    }

                    // InitializeEncoder
                    const err = mod._opus_encoder_init(
                        this.encoderPtr,
                        this.sampleRate,
                        this.channels,
                        application
                    );

                    if (err < 0) {
                        throw new Error(`OpusEncoderInitializeFail: ${err}`);
                    }

                    // Setbitrate (16kbps)
                    mod._opus_encoder_ctl(this.encoderPtr, 4002, 16000); // OPUS_SET_BITRATE

                    // Setcomplexity (0-10, higherQualitybetter butCPUUsemore)
                    mod._opus_encoder_ctl(this.encoderPtr, 4010, 5);     // OPUS_SET_COMPLEXITY

                    // SetUseDTX (notTransferMuteframe)
                    mod._opus_encoder_ctl(this.encoderPtr, 4016, 1);     // OPUS_SET_DTX

                    log("OpusEncoderInitializeSuccess", 'success');
                    return true;
                } catch (error) {
                    if (this.encoderPtr) {
                        mod._free(this.encoderPtr);
                        this.encoderPtr = null;
                    }
                    log(`OpusEncoderInitializeFail: ${error.message}`, 'error');
                    return false;
                }
            },

            // EncodePCMDataforOpus
            encode: function (pcmData) {
                if (!this.encoderPtr) {
                    if (!this.init()) {
                        return null;
                    }
                }

                try {
                    const mod = this.module;

                    // forPCMDataallocate memory
                    const pcmPtr = mod._malloc(pcmData.length * 2); // 2Byte/int16

                    // willPCMDataCopytoHEAP
                    for (let i = 0; i < pcmData.length; i++) {
                        mod.HEAP16[(pcmPtr >> 1) + i] = pcmData[i];
                    }

                    // forOutputallocate memory
                    const outPtr = mod._malloc(this.maxPacketSize);

                    // Encode
                    const encodedLen = mod._opus_encode(
                        this.encoderPtr,
                        pcmPtr,
                        this.frameSize,
                        outPtr,
                        this.maxPacketSize
                    );

                    if (encodedLen < 0) {
                        throw new Error(`OpusEncodeFail: ${encodedLen}`);
                    }

                    // CopyEncodeafterData
                    const opusData = new Uint8Array(encodedLen);
                    for (let i = 0; i < encodedLen; i++) {
                        opusData[i] = mod.HEAPU8[outPtr + i];
                    }

                    // Releasememory
                    mod._free(pcmPtr);
                    mod._free(outPtr);

                    return opusData;
                } catch (error) {
                    log(`OpusEncodeerror occurred: ${error.message}`, 'error');
                    return null;
                }
            },

            // destroyEncoder
            destroy: function () {
                if (this.encoderPtr) {
                    this.module._free(this.encoderPtr);
                    this.encoderPtr = null;
                }
            }
        };

        opusEncoder.init();
        return opusEncoder;
    } catch (error) {
        log(`CreateOpusEncoderFail: ${error.message}`, 'error');
        return false;
    }
}