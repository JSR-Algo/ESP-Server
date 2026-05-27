import BlockingQueue from '../../utils/blocking-queue.js?v=0205';
import { log } from '../../utils/logger.js?v=0205';

// AudiostreamPlayContextclass
export class StreamingContext {
    constructor(opusDecoder, audioContext, sampleRate, channels, minAudioDuration) {
        this.opusDecoder = opusDecoder;
        this.audioContext = audioContext;

        // AudioParameter
        this.sampleRate = sampleRate;
        this.channels = channels;
        this.minAudioDuration = minAudioDuration;

        // Initializequeue andStatus
        this.queue = [];          // alreadyDecodeofPCMqueue. currentlyPlay
        this.activeQueue = new BlockingQueue(); // alreadyDecodeofPCMqueue.PreparePlay
        this.pendingAudioBufferQueue = [];  // waitProcessCache ofqueue
        this.audioBufferQueue = new BlockingQueue();  // Cachequeue
        this.playing = false;     // WhetherrunningPlay
        this.endOfStream = false; // WhetherReceiveEndsignal
        this.source = null;       // CurrentAudiosource
        this.totalSamples = 0;    // accumulated total sample count
        this.lastPlayTime = 0;    // lastPlayTime oftimestamp
        this.scheduledEndTime = 0; // scheduledAudioofEndTime

        // Initializeanalyzer node (forLive2DUse)
        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 256;
    }

    // CacheAudioArray
    pushAudioBuffer(item) {
        this.audioBufferQueue.enqueue(...item);
    }

    // GetNeedProcessCachequeue, single-thread: inaudioBufferQueuealwaysUpdateofStatusunder will not have safetyIssue
    async getPendingAudioBufferQueue() {
        // WaitDataarrive andGet
        const data = await this.audioBufferQueue.dequeue();
        // assign to pendingProcessqueue
        this.pendingAudioBufferQueue = data;
    }

    // GetrunningPlayalreadyDecodeofPCMqueue, single-thread: inactiveQueuealwaysUpdateofStatusunder will not have safetyIssue
    async getQueue(minSamples) {
        const num = minSamples - this.queue.length > 0 ? minSamples - this.queue.length : 1;

        // WaitDataandGet
        const tempArray = await this.activeQueue.dequeue(num);
        this.queue.push(...tempArray);
    }

    // willInt16AudioConvert data toFloat32Audio Data
    convertInt16ToFloat32(int16Data) {
        const float32Data = new Float32Array(int16Data.length);
        for (let i = 0; i < int16Data.length; i++) {
            // will[-32768,32767]RangeConvert to[-1,1],Use Unified32768.0avoid asymmetric distortion
            float32Data[i] = int16Data[i] / 32768.0;
        }
        return float32Data;
    }

    // GetwaitDecodepacket count
    getPendingDecodeCount() {
        return this.audioBufferQueue.length + this.pendingAudioBufferQueue.length;
    }

    // GetwaitPlaysample count (Convert topacket count, each packet960samples)
    getPendingPlayCount() {
        // Calculatealready in queueInsamples
        const queuedSamples = this.activeQueue.length + this.queue.length;

        // Calculatescheduled but notPlaysamples (inWeb AudioBufferin)
        let scheduledSamples = 0;
        if (this.playing && this.scheduledEndTime) {
            const currentTime = this.audioContext.currentTime;
            const remainingTime = Math.max(0, this.scheduledEndTime - currentTime);
            scheduledSamples = Math.floor(remainingTime * this.sampleRate);
        }

        const totalSamples = queuedSamples + scheduledSamples;
        return Math.ceil(totalSamples / 960);
    }

    // Clear AllAudiobuffer
    clearAllBuffers() {
        log('Clear AllAudiobuffer', 'info');

        // Clear Allqueue (UseclearMethodKeepObjectQuote)
        this.audioBufferQueue.clear();
        this.pendingAudioBufferQueue = [];
        this.activeQueue.clear();
        this.queue = [];

        // StopCurrentPlayAudio ofsource
        if (this.source) {
            try {
                this.source.stop();
                this.source.disconnect();
            } catch (e) {
                // IgnorealreadyStoperror of
            }
            this.source = null;
        }

        // ResetStatus
        this.playing = false;
        this.scheduledEndTime = this.audioContext.currentTime;
        this.totalSamples = 0;

        log('Audiobuffer alreadyClear', 'success');
    }

    // Getanalyzer node (forLive2DUse)
    getAnalyser() {
        return this.analyser;
    }

    // willOpusDataDecode asPCM
    async decodeOpusFrames() {
        if (!this.opusDecoder) {
            log('OpusDecodedevice not yetInitialize, unableDecode', 'error');
            return;
        } else {
            log('OpusDecodedeviceStart', 'info');
        }

        while (true) {
            let decodedSamples = [];
            for (const frame of this.pendingAudioBufferQueue) {
                try {
                    // UseOpusDecodedeviceDecode
                    const frameData = this.opusDecoder.decode(frame);
                    if (frameData && frameData.length > 0) {
                        // Convert toFloat32
                        const floatData = this.convertInt16ToFloat32(frameData);
                        // UseLoopreplace spread operator
                        for (let i = 0; i < floatData.length; i++) {
                            decodedSamples.push(floatData[i]);
                        }
                    }
                } catch (error) {
                    log("OpusDecodeFail: " + error.message, 'error');
                }
            }

            if (decodedSamples.length > 0) {
                // UseLoopreplace spread operator
                for (let i = 0; i < decodedSamples.length; i++) {
                    this.activeQueue.enqueue(decodedSamples[i]);
                }
                this.totalSamples += decodedSamples.length;
            } else {
                log('NoneSuccessDecodesamples of', 'warning');
            }
            await this.getPendingAudioBufferQueue();
        }
    }

    // StartPlayAudio
    async startPlaying() {
        this.scheduledEndTime = this.audioContext.currentTime; // track scheduledAudioofEndTime

        while (true) {
            // initial buffer:Waitenough samples thenStartPlay
            const minSamples = this.sampleRate * this.minAudioDuration * 2;
            if (!this.playing && this.queue.length < minSamples) {
                await this.getQueue(minSamples);
            }
            this.playing = true;

            // continuePlayin queueAudio of, each timePlaysmall chunk
            while (this.playing && this.queue.length > 0) {
                // each timePlay120msAudio of(2countOpuspacket)
                const playDuration = 0.12;
                const targetSamples = Math.floor(this.sampleRate * playDuration);
                const actualSamples = Math.min(this.queue.length, targetSamples);

                if (actualSamples === 0) break;

                const currentSamples = this.queue.splice(0, actualSamples);
                const audioBuffer = this.audioContext.createBuffer(this.channels, currentSamples.length, this.sampleRate);
                audioBuffer.copyToChannel(new Float32Array(currentSamples), 0);

                // CreateAudiosource
                this.source = this.audioContext.createBufferSource();
                this.source.buffer = audioBuffer;

                // precise schedulingPlayTime
                const currentTime = this.audioContext.currentTime;
                const startTime = Math.max(this.scheduledEndTime, currentTime);

                // Connectto analyzer andOutput
                this.source.connect(this.analyser);
                this.source.connect(this.audioContext.destination);

                log(`schedulePlay ${currentSamples.length} samples, about ${(currentSamples.length / this.sampleRate).toFixed(2)} seconds`, 'debug');
                this.source.start(startTime);

                // UpdatenextAudiochunk schedulingTime
                const duration = audioBuffer.duration;
                this.scheduledEndTime = startTime + duration;
                this.lastPlayTime = startTime;

                // Ifin queueDataInsufficient,WaitnewData
                if (this.queue.length < targetSamples) {
                    break;
                }
            }

            // WaitnewData
            await this.getQueue(minSamples);
        }
    }
}

// CreatestreamingContextInstancefactory ofFunction
export function createStreamingContext(opusDecoder, audioContext, sampleRate, channels, minAudioDuration) {
    return new StreamingContext(opusDecoder, audioContext, sampleRate, channels, minAudioDuration);
}