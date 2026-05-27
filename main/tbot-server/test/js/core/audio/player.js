// AudioPlayModule
import BlockingQueue from '../../utils/blocking-queue.js?v=0205';
import { log } from '../../utils/logger.js?v=0205';
import { createStreamingContext } from './stream-context.js?v=0205';

// AudioPlayprocessor class
export class AudioPlayer {
    constructor() {
        // AudioParameter
        this.SAMPLE_RATE = 16000;
        this.CHANNELS = 1;
        this.FRAME_SIZE = 960;
        this.MIN_AUDIO_DURATION = 0.12;

        // Status
        this.audioContext = null;
        this.opusDecoder = null;
        this.streamingContext = null;
        this.queue = new BlockingQueue();
        this.isPlaying = false;
    }

    // Get or createAudioContext
    getAudioContext() {
        if (!this.audioContext) {
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: this.SAMPLE_RATE,
                latencyHint: 'interactive'
            });
            log('CreateAudio context,Sample rate: ' + this.SAMPLE_RATE + 'Hz', 'debug');
        }
        return this.audioContext;
    }

    // InitializeOpusDecodedevice
    async initOpusDecoder() {
        if (this.opusDecoder) return this.opusDecoder;

        try {
            if (typeof window.ModuleInstance === 'undefined') {
                if (typeof Module !== 'undefined') {
                    window.ModuleInstance = Module;
                    log('UseglobalModuleAsModuleInstance', 'info');
                } else {
                    throw new Error('Opuslibrary notLoad,ModuleInstanceandModuleObjectallDoes not exist');
                }
            }

            const mod = window.ModuleInstance;

            this.opusDecoder = {
                channels: this.CHANNELS,
                rate: this.SAMPLE_RATE,
                frameSize: this.FRAME_SIZE,
                module: mod,
                decoderPtr: null,

                init: function () {
                    if (this.decoderPtr) return true;

                    const decoderSize = mod._opus_decoder_get_size(this.channels);
                    log(`OpusDecodedeviceSize: ${decoderSize}Byte`, 'debug');

                    this.decoderPtr = mod._malloc(decoderSize);
                    if (!this.decoderPtr) {
                        throw new Error("unable allocateDecodeprocessor memory");
                    }

                    const err = mod._opus_decoder_init(
                        this.decoderPtr,
                        this.rate,
                        this.channels
                    );

                    if (err < 0) {
                        this.destroy();
                        throw new Error(`OpusDecodedeviceInitializeFail: ${err}`);
                    }

                    log("OpusDecodedeviceInitializeSuccess", 'success');
                    return true;
                },

                decode: function (opusData) {
                    if (!this.decoderPtr) {
                        if (!this.init()) {
                            throw new Error("Decodedevice not yetInitializeand unableInitialize");
                        }
                    }

                    try {
                        const mod = this.module;

                        const opusPtr = mod._malloc(opusData.length);
                        mod.HEAPU8.set(opusData, opusPtr);

                        const pcmPtr = mod._malloc(this.frameSize * 2);

                        const decodedSamples = mod._opus_decode(
                            this.decoderPtr,
                            opusPtr,
                            opusData.length,
                            pcmPtr,
                            this.frameSize,
                            0
                        );

                        if (decodedSamples < 0) {
                            mod._free(opusPtr);
                            mod._free(pcmPtr);
                            throw new Error(`OpusDecodeFail: ${decodedSamples}`);
                        }

                        const decodedData = new Int16Array(decodedSamples);
                        for (let i = 0; i < decodedSamples; i++) {
                            decodedData[i] = mod.HEAP16[(pcmPtr >> 1) + i];
                        }

                        mod._free(opusPtr);
                        mod._free(pcmPtr);

                        return decodedData;
                    } catch (error) {
                        log(`OpusDecodeerror: ${error.message}`, 'error');
                        return new Int16Array(0);
                    }
                },

                destroy: function () {
                    if (this.decoderPtr) {
                        this.module._free(this.decoderPtr);
                        this.decoderPtr = null;
                    }
                }
            };

            if (!this.opusDecoder.init()) {
                throw new Error("OpusDecodedeviceInitializeFail");
            }

            return this.opusDecoder;

        } catch (error) {
            log(`OpusDecodedeviceInitializeFail: ${error.message}`, 'error');
            this.opusDecoder = null;
            throw error;
        }
    }

    // StartAudiobuffer
    async startAudioBuffering() {
        log("StartAudiobuffer...", 'info');

        this.initOpusDecoder().catch(error => {
            log(`preInitializeOpusDecodedeviceFail: ${error.message}`, 'warning');
        });

        const timeout = 400;
        while (true) {
            const packets = await this.queue.dequeue(
                6,
                timeout,
                (count) => {
                    log(`buffer timeout,Currentbuffer packet count: ${count},StartPlay`, 'info');
                }
            );
            if (packets.length) {
                log(`buffered ${packets.length} countAudiopackets,StartPlay`, 'info');
                this.streamingContext.pushAudioBuffer(packets);
            }

            while (true) {
                const data = await this.queue.dequeue(99, 30);
                if (data.length) {
                    this.streamingContext.pushAudioBuffer(data);
                } else {
                    break;
                }
            }
        }
    }

    // PlaybufferedAudio of
    async playBufferedAudio() {
        try {
            this.audioContext = this.getAudioContext();

            if (!this.opusDecoder) {
                log('InitializeOpusDecodedevice...', 'info');
                try {
                    this.opusDecoder = await this.initOpusDecoder();
                    if (!this.opusDecoder) {
                        throw new Error('DecodedeviceInitializeFail');
                    }
                    log('OpusDecodedeviceInitializeSuccess', 'success');
                } catch (error) {
                    log('OpusDecodedeviceInitializeFail: ' + error.message, 'error');
                    this.isPlaying = false;
                    return;
                }
            }

            if (!this.streamingContext) {
                this.streamingContext = createStreamingContext(
                    this.opusDecoder,
                    this.audioContext,
                    this.SAMPLE_RATE,
                    this.CHANNELS,
                    this.MIN_AUDIO_DURATION
                );
            }

            this.streamingContext.decodeOpusFrames();
            this.streamingContext.startPlaying();

        } catch (error) {
            log(`PlaybufferedAudio oferror occurred: ${error.message}`, 'error');
            this.isPlaying = false;
            this.streamingContext = null;
        }
    }

    // AddAudio Datato queue
    enqueueAudioData(opusData) {
        if (opusData.length > 0) {
            this.queue.enqueue(opusData);
        } else {
            log('ReceiveemptyAudio Dataframe, may beEndFlag', 'warning');
            if (this.isPlaying && this.streamingContext) {
                this.streamingContext.endOfStream = true;
            }
        }
    }

    // preLoadDecodedevice
    async preload() {
        log('preLoadOpusDecodedevice...', 'info');
        try {
            await this.initOpusDecoder();
            log('OpusDecodeprocessor preLoadSuccess', 'success');
        } catch (error) {
            log(`OpusDecodeprocessor preLoadFail: ${error.message}, will beNeedretry when`, 'warning');
        }
    }

    // StartPlaySystem
    async start() {
        await this.preload();
        this.playBufferedAudio();
        this.startAudioBuffering();
    }

    // Get audiopacketStatsinfo
    getAudioStats() {
        if (!this.streamingContext) {
            return {
                pendingDecode: 0,
                pendingPlay: 0,
                totalPending: 0
            };
        }

        const pendingDecode = this.streamingContext.getPendingDecodeCount();
        const pendingPlay = this.streamingContext.getPendingPlayCount();

        return {
            pendingDecode,  // waitDecodepacket count
            pendingPlay,    // waitPlaypacket count
            totalPending: pendingDecode + pendingPlay  // total waitingProcesspacket count
        };
    }

    // Clear AllAudiobuffer andStopPlay
    clearAllAudio() {
        log('AudioPlayer: Clear AllAudio', 'info');

        // Clearreceive queue (UseclearMethodKeepObjectQuote)
        this.queue.clear();

        // ClearstreamContextall buffers of
        if (this.streamingContext) {
            this.streamingContext.clearAllBuffers();
        }

        log('AudioPlayer: AudioalreadyClear', 'success');
    }
}

// Createsingleton
let audioPlayerInstance = null;

export function getAudioPlayer() {
    if (!audioPlayerInstance) {
        audioPlayerInstance = new AudioPlayer();
    }
    return audioPlayerInstance;
}
