/**
 * @module speech-playback
 * Responsibilities: Validate streamed TTS records and play continuous browser PCM.
 * Implementation: Require ordered start/audio/end records; schedule 24 kHz PCM buffers on one AudioContext clock after a small initial buffer. Stop explicitly on malformed/truncated audio or underrun.
 * Related Modules: interview-voice owns question epochs and cancellation; speech.streaming emits authenticated NDJSON. No UE or microphone resources are used here.
 * Declaration Index:
 * - readSpeechStream: Read bounded NDJSON and validate format, sequencing and completion.
 * - readSpeechStream.deliver: Validate one record before invoking the appropriate callback.
 * - PcmStreamPlayer: Own one utterance's Web Audio buffers and playback lifecycle.
 * - PcmStreamPlayer.constructor: Initialize bounded playback state and lifecycle callbacks.
 * - PcmStreamPlayer.prepare: Create and resume the context before starting a paid request.
 * - PcmStreamPlayer.prepare.callback1: Install cancellation for a pending browser autoplay decision.
 * - PcmStreamPlayer.prepare.callback1.onAbort: Reject the resume wait when the request is cancelled.
 * - PcmStreamPlayer.append: Convert aligned little-endian PCM and buffer or schedule it.
 * - PcmStreamPlayer.begin: Schedule initial contiguous audio after the prebuffer threshold.
 * - PcmStreamPlayer.begin.callback1: Report actual scheduled playback start unless cancelled.
 * - PcmStreamPlayer.schedule: Queue one buffer on the uninterrupted audio clock.
 * - PcmStreamPlayer.schedule.source.onended: Finish once all scheduled audio drains or report underrun.
 * - PcmStreamPlayer.finish: Mark verified input completion and release a short initial utterance.
 * - PcmStreamPlayer.fail: Stop resources before reporting one playback failure.
 * - PcmStreamPlayer.stop: Cancel every queued source and close the utterance's audio context.
 * - PcmStreamPlayer.stop.callback1: Log a context-close failure without exposing speech data.
 * Variable Index:
 * - SAMPLE_RATE: Required provider PCM rate; browser hardware resampling is handled by Web Audio.
 * - MAX_PCM_BYTES: Preserve the existing maximum 120-second utterance.
 * - MAX_CHUNK_BYTES: Match the server's maximum PCM fragment size.
 * - MAX_LINE_CHARS: Bound JSON framing before parsing.
 * - PREBUFFER_SAMPLES: Initial 240 ms audio reserve, without changing provider speech timing.
 * - SCHEDULE_LEAD_SECONDS: Give the audio thread 60 ms to start its first scheduled buffer.
 */

const SAMPLE_RATE = 24000;
const MAX_PCM_BYTES = SAMPLE_RATE * 2 * 120;
const MAX_CHUNK_BYTES = 8192;
const MAX_LINE_CHARS = 16384;
const PREBUFFER_SAMPLES = SAMPLE_RATE * 0.24;
const SCHEDULE_LEAD_SECONDS = 0.06;

/** Functionality: Parse a single bounded stream and enforce its terminal contract.
 * Inputs: Fetch Response, onStart/onAudio/onEnd callbacks, optional AbortSignal.
 * Outputs: Resolves after verified end plus transport EOF; throws on HTTP/protocol/provider errors.
 * Logic: Decode UTF-8 incrementally, validate each newline record and account exact PCM bytes.
 * Constraints: Never accept truncated EOF as success, retry, or concatenate another utterance.
 */
export async function readSpeechStream(response, { onStart, onAudio, onEnd, signal }) {
  if (!response.ok) {
    const body = await response.json();
    throw new Error(body.error?.detail || `Speech request failed (${response.status}).`);
  }
  if (!response.headers.get("Content-Type")?.startsWith("application/x-ndjson") || !response.body) {
    throw new Error("Speech streaming response is unavailable.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let pending = "", id = null, sequence = 0, bytes = 0, terminal = null;
  /** Functionality: Validate a complete NDJSON record before dispatch.
   * Inputs: One nonempty JSON line and stream-local sequence/byte state.
   * Outputs: Callback invocation or a protocol/provider exception.
   * Logic: Start fixes format/identity; audio increments exact sample accounting; end validates totals.
   * Constraints: Payloads cannot exceed the server chunk cap or follow a terminal record.
   */
  function deliver(line) {
    if (line.length > MAX_LINE_CHARS || terminal) throw new Error("Invalid speech stream framing.");
    const event = JSON.parse(line);
    if (event?.type === "error") throw new Error(event.error?.detail || "Speech synthesis failed.");
    if (event?.type === "start") {
      if (id || typeof event.utterance_id !== "string" || !/^[0-9a-f-]{36}$/.test(event.utterance_id)
          || event.sample_rate !== SAMPLE_RATE || event.channels !== 1 || event.sample_width !== 2) {
        throw new Error("Unsupported speech stream format.");
      }
      id = event.utterance_id;
      onStart(event);
    } else if (event?.type === "audio") {
      if (!id || event.sequence !== sequence || typeof event.pcm !== "string"
          || event.pcm.length > Math.ceil(MAX_CHUNK_BYTES / 3) * 4) {
        throw new Error("Speech audio arrived out of sequence.");
      }
      const binary = atob(event.pcm);
      if (!binary.length || binary.length % 2 || binary.length > MAX_CHUNK_BYTES
          || bytes + binary.length > MAX_PCM_BYTES) throw new Error("Invalid speech PCM size.");
      const pcm = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index++) pcm[index] = binary.charCodeAt(index);
      bytes += pcm.length;
      sequence++;
      onAudio(pcm);
    } else if (event?.type === "end") {
      if (!id || event.utterance_id !== id || !bytes || event.audio_bytes !== bytes) {
        throw new Error("Speech completion does not match the received audio.");
      }
      terminal = event;
    } else {
      throw new Error("Invalid speech stream event.");
    }
  }
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException("Speech cancelled.", "AbortError");
      const { value, done } = await reader.read();
      pending += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let newline;
      while ((newline = pending.indexOf("\n")) >= 0) {
        const line = pending.slice(0, newline);
        pending = pending.slice(newline + 1);
        if (!line) throw new Error("Empty speech stream record.");
        deliver(line);
      }
      if (pending.length > MAX_LINE_CHARS) throw new Error("Speech stream record is too large.");
      if (done) break;
    }
    if (pending || !terminal) throw new Error("Speech stream ended before synthesis completed.");
    onEnd(terminal);
  } finally {
    try {
      await reader.cancel();
    } catch (error) {
      console.warn("Speech stream reader cleanup failed", { name: error.name });
    } finally {
      reader.releaseLock();
    }
  }
}

/** Functionality: Play one validated PCM stream with bounded initial buffering.
 * Logic: Queue adjacent AudioBufferSourceNodes on one clock; terminal input and drained audio are distinct states.
 * Constraints: No automatic replay or silence insertion. Underrun is a visible error and stops the utterance.
 */
export class PcmStreamPlayer {
  /** Inputs: onStarted/onEnded/onError callbacks. Outputs: An idle player with no device activity.
   * Logic: Track pending samples, scheduled sources, total bytes and terminal state per utterance.
   * Constraints: A stopped instance is never reused.
   */
  constructor({ onStarted, onEnded, onError }) {
    this.onStarted = onStarted;
    this.onEnded = onEnded;
    this.onError = onError;
    this.context = null;
    this.pending = [];
    this.pendingSamples = 0;
    this.totalBytes = 0;
    this.sources = new Set();
    this.nextTime = null;
    this.startTimer = null;
    this.inputEnded = false;
    this.stopped = false;
  }

  /** Inputs: Optional AbortSignal, browser AudioContext capability and autoplay policy. Outputs: Running context or exception.
   * Logic: Resume before fetching speech so denied playback does not spend a model request.
   * Constraints: Cancellation bounds an unresolved autoplay decision; no microphone or alternate playback implementation.
   */
  async prepare(signal) {
    const Context = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!Context) throw new Error("This browser does not support streaming audio playback.");
    if (this.stopped || signal?.aborted) throw new DOMException("Speech cancelled.", "AbortError");
    this.context = new Context();
    let onAbort;
    const cancellation = new Promise(/** Bound browser autoplay waits by the owning request signal. */ (_resolve, reject) => {
      /** Reject only this pending resume; its caller owns final resource cleanup. */
      onAbort = () => reject(new DOMException("Speech cancelled.", "AbortError"));
      signal?.addEventListener("abort", onAbort, { once: true });
    });
    try {
      await Promise.race([this.context.resume(), cancellation]);
    } finally {
      signal?.removeEventListener("abort", onAbort);
    }
    if (this.stopped) throw new DOMException("Speech cancelled.", "AbortError");
    if (this.context.state !== "running") throw new Error("Enable audio playback in your browser.");
  }

  /** Inputs: Validated little-endian signed 16-bit mono PCM. Outputs: Buffered or scheduled audio.
   * Logic: Normalize samples to floats without changing their rate, order, count or gain.
   * Constraints: Enforce per-fragment and total bounds independently of the transport parser.
   */
  append(pcm) {
    if (this.stopped) return;
    if (this.inputEnded || !this.context || !pcm.length || pcm.length % 2
        || pcm.length > MAX_CHUNK_BYTES || this.totalBytes + pcm.length > MAX_PCM_BYTES) {
      throw new Error("Invalid browser speech buffer.");
    }
    const samples = new Float32Array(pcm.length / 2);
    const view = new DataView(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    for (let index = 0; index < samples.length; index++) samples[index] = view.getInt16(index * 2, true) / 32768;
    this.totalBytes += pcm.length;
    if (this.nextTime === null) {
      this.pending.push(samples);
      this.pendingSamples += samples.length;
      if (this.pendingSamples >= PREBUFFER_SAMPLES) this.begin();
    } else {
      this.schedule(samples);
    }
  }

  /** Inputs: Initial pending samples and running context. Outputs: Contiguous scheduled sources plus one start callback.
   * Logic: Reserve 60 ms of audio-thread lead, then schedule pending data at exact sample boundaries.
   * Constraints: A short utterance starts only after explicit end confirms its complete content.
   */
  begin() {
    if (this.stopped || this.nextTime !== null || !this.pendingSamples) return;
    this.nextTime = this.context.currentTime + SCHEDULE_LEAD_SECONDS;
    for (const samples of this.pending) this.schedule(samples);
    this.pending = [];
    this.pendingSamples = 0;
    this.startTimer = setTimeout(/** Report scheduled audible start only for the live player. */ () => {
      this.startTimer = null;
      if (!this.stopped) this.onStarted();
    }, SCHEDULE_LEAD_SECONDS * 1000);
  }

  /** Inputs: Float samples and the next contiguous AudioContext timestamp. Outputs: One queued source.
   * Logic: Start the buffer exactly when its predecessor ends and retain it for interruption.
   * Constraints: Late data cannot shift the playback clock or insert unreported silence.
   */
  schedule(samples) {
    if (this.nextTime < this.context.currentTime) throw new Error("Speech playback was interrupted. Please replay the question.");
    const buffer = this.context.createBuffer(1, samples.length, SAMPLE_RATE);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.sources.add(source);
    /** Complete only a verified and fully drained stream; otherwise stop on underrun. */
    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      if (this.stopped || this.sources.size) return;
      if (this.inputEnded) {
        this.stop();
        this.onEnded();
      } else {
        this.fail(new Error("Speech audio stopped arriving before synthesis completed."));
      }
    };
    source.start(this.nextTime);
    this.nextTime += samples.length / SAMPLE_RATE;
  }

  /** Inputs: Previously validated stream end. Outputs: Marks completion or starts a short buffered utterance.
   * Logic: Playback completion remains driven by the final source's ended event.
   * Constraints: Empty or repeated completion is invalid; no synthetic padding.
   */
  finish() {
    if (this.stopped) return;
    if (this.inputEnded || !this.totalBytes) throw new Error("Invalid speech completion.");
    this.inputEnded = true;
    if (this.nextTime === null) this.begin();
  }

  /** Inputs: Playback error. Outputs: One error callback after every queued source is stopped.
   * Logic: Stop first to prevent late audio while the owning question reports failure.
   * Constraints: A stopped player never emits a second error or completion.
   */
  fail(error) {
    if (this.stopped) return;
    this.stop();
    this.onError(error);
  }

  /** Inputs: Owned context, timer and sources. Outputs: Immediate cancellation and asynchronous context cleanup.
   * Logic: Detach ended callbacks before stopping scheduled sources; discard pending samples.
   * Constraints: Idempotent; never stops microphone or avatar resources.
   */
  stop() {
    if (this.stopped) return;
    this.stopped = true;
    clearTimeout(this.startTimer);
    for (const source of this.sources) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    this.sources.clear();
    this.pending = [];
    if (this.context && this.context.state !== "closed") {
      this.context.close().catch(/** Surface a cleanup failure without speech data. */ (error) => {
        console.warn("Speech audio context cleanup failed", { name: error.name });
      });
    }
  }
}
