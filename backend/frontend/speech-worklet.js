/**
 *
 * @module speech-worklet
 * Responsibilities: Capture microphone blocks in an AudioWorklet, resample them, and transfer bounded PCM chunks to the main thread.
 * Implementation: Keep output silent, buffer resampled samples until a chunk threshold or explicit flush, and acknowledge only after remaining samples are transferred.
 * Related Modules: speech-capture.js creates and controls this worklet; pcm-resampler.js performs cross-block sample-rate conversion.
 *
 * Declaration Index:
 * - SpeechCaptureProcessor:
 *   A silent output keeps capture alive without feeding the microphone into speakers.
 * - SpeechCaptureProcessor.constructor:
 *   Initialize a silent worklet and handle explicit end-of-input flushing.
 * - SpeechCaptureProcessor.constructor.this.port.onmessage:
 *   Send all remaining PCM before the flushed acknowledgement.
 * - SpeechCaptureProcessor.flush:
 *   Transfer the accumulated little-endian PCM buffer to the main thread.
 * - SpeechCaptureProcessor.flush.callback1:
 *   Write one signed sample using the required little-endian byte order.
 * - SpeechCaptureProcessor.process:
 *   Silence outputs and resample microphone blocks into bounded PCM messages.
 *
 * Variable Index:
 * None
 *
 */
import { PCM16Resampler } from "./pcm-resampler.js";

/** A silent output keeps capture alive without feeding the microphone into speakers. */
class SpeechCaptureProcessor extends AudioWorkletProcessor {
  /** Initialize a silent worklet and handle explicit end-of-input flushing. */ constructor() {
    super();
    this.resampler = new PCM16Resampler(sampleRate);
    this.pending = [];
    this.stopped = false;
    /** Send all remaining PCM before the flushed acknowledgement. */ this.port.onmessage = ({ data }) => {
      if (data === "flush") {
        this.stopped = true;
        this.flush();
        this.port.postMessage({ type: "flushed" });
      }
    };
  }

  /** Transfer the accumulated little-endian PCM buffer to the main thread. */ flush() {
    if (!this.pending.length) return;
    const buffer = new ArrayBuffer(this.pending.length * 2);
    const view = new DataView(buffer);
    /** Write one signed sample using the required little-endian byte order. */ this.pending.forEach((value, index) => view.setInt16(index * 2, value, true));
    this.pending = [];
    this.port.postMessage({ type: "pcm", buffer }, [buffer]);
  }

  /** Silence outputs and resample microphone blocks into bounded PCM messages. */ process(inputs, outputs) {
    for (const channel of outputs?.[0] ?? []) channel.fill(0);
    if (!this.stopped && inputs[0]?.[0]) {
      this.pending.push(...this.resampler.process(inputs[0][0]));
      if (this.pending.length >= 1600) this.flush();
    }
    return !this.stopped;
  }
}

registerProcessor("speech-capture", SpeechCaptureProcessor);
