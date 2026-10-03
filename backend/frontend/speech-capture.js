/**
 * @module speech-capture
 * 职责：PCM 采集及有界 STT 传输；通过回调交付转写，不直接提交面试回答。
 * 实现：麦克风授权/握手后采集，flush 后发送 stop；时限收尾不代表用户确认提交。
 * 关联：interview-voice.js 接收部分/最终文本并控制字幕和结束确认。
 *
 * 目录：
 * - SpeechCapture：
 *   Browser PCM capture and one STT task; completion delivers text to the caller, never an interview command.
 * - SpeechCapture.constructor：
 *   Store transcript callbacks and initialize one capture session's resources.
 * - SpeechCapture.start：
 *   Obtain microphone permission and start PCM capture after the STT handshake.
 * - SpeechCapture.start.callback1：
 *   Wait for recognition startup and attach bounded socket lifecycle handlers.
 * - SpeechCapture.start.callback1.callback1：
 *   Reject the pending startup after its deadline.
 * - SpeechCapture.start.callback1.socket.onmessage：
 *   Process draft, final and error messages without submitting an interview answer.
 * - SpeechCapture.start.callback1.socket.onerror：
 *   Report a recognition transport failure and release the microphone.
 * - SpeechCapture.start.callback1.socket.onclose：
 *   Detect a connection that ended without a final transcript.
 * - SpeechCapture.start.this.node.port.onmessage：
 *   Forward PCM with a backlog limit and acknowledge worklet flushing.
 * - SpeechCapture.start.callback2：
 *   End recording automatically at the fixed 120-second capture limit.
 * - SpeechCapture.end：
 *   Flush audio before sending stop, then wait for the provider's final transcript.
 * - SpeechCapture.end.callback1：
 *   Wait for the worklet's final PCM to precede the stop message.
 * - SpeechCapture.end.callback1.callback1：
 *   Fail capture if the worklet cannot flush promptly.
 * - SpeechCapture.end.callback1.this.flushResolve：
 *   Resolve the flush acknowledgement and clear its timer.
 * - SpeechCapture.end.callback2：
 *   Report a provider that failed to finalize the ended answer.
 * - SpeechCapture.fail：
 *   Reject startup and release resources after a public error message.
 * - SpeechCapture.releaseAudio：
 *   Stop microphone tracks, detach the worklet and close its audio context.
 * - SpeechCapture.releaseAudio.callback1：
 *   Stop one acquired microphone track.
 * - SpeechCapture.close：
 *   Invalidate socket callbacks and end the local capture lifecycle.
 *
 * 关键变量：
 * （无模块级变量。）
 */
/** Browser PCM capture and one STT task; completion delivers text to the caller, never an interview command. */
export class SpeechCapture {
  /** Store transcript callbacks and initialize one capture session's resources. */ constructor(onPartial, onFinal, onError) {
    this.onPartial = onPartial;
    this.onFinal = onFinal;
    this.onError = onError;
    this.closed = false;
    this.recording = false;
    this.socket = null;
    this.context = null;
    this.stream = null;
    this.node = null;
    this.timer = null;
    this.finalTimer = null;
    this.flushResolve = null;
    this.startReject = null;
    this.starting = null;
  }

  /** Obtain microphone permission and start PCM capture after the STT handshake. */ async start() {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Use localhost or HTTPS for microphone access.");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      }});
      if (this.closed) { await this.close(); return; }
      this.context = new AudioContext();
      await this.context.resume();
      await this.context.audioWorklet.addModule("/stream-demo/speech-worklet.js");
      if (this.closed) { await this.close(); return; }
      const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/speech/stt/`);
      this.socket = socket;
      socket.binaryType = "arraybuffer";
      this.starting = new Promise(/** Wait for recognition startup and attach bounded socket lifecycle handlers. */ (resolve, reject) => {
        this.startReject = reject;
        const timeout = setTimeout(/** Reject the pending startup after its deadline. */ () => reject(new Error("Speech recognition start timed out.")), 17000);
        /** Process draft, final and error messages without submitting an interview answer. */ socket.onmessage = ({ data }) => {
          if (this.closed) return;
          try {
            const message = JSON.parse(data);
            if (message.type === "hello") socket.send(JSON.stringify({ type: "start" }));
            if (message.type === "started") { clearTimeout(timeout); this.startReject = null; resolve(); }
            if (message.type === "partial") this.onPartial(message.text);
            if (message.type === "final") { this.onFinal(message.text, message.finalization_ms); void this.close(); }
            if (message.type === "error") this.fail(new Error(`${message.code}: ${message.detail}`));
          } catch (error) { this.fail(error); }
        };
        /** Report a recognition transport failure and release the microphone. */ socket.onerror = () => this.fail(new Error("Speech connection failed; check the service and start a new recording."));
        /** Detect a connection that ended without a final transcript. */ socket.onclose = () => { clearTimeout(timeout); if (!this.closed) this.fail(new Error("Speech ended without a final transcript.")); };
      });
      await this.starting;
      if (this.closed) { await this.close(); return; }
      this.node = new AudioWorkletNode(this.context, "speech-capture");
      /** Forward PCM with a backlog limit and acknowledge worklet flushing. */ this.node.port.onmessage = ({ data }) => {
        if (data.type === "flushed") this.flushResolve?.();
        if (data.type === "pcm" && !this.closed) {
          if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 1024 * 1024) {
            this.fail(new Error("Speech connection is not keeping up; check the connection and start a new recording."));
          } else socket.send(data.buffer);
        }
      };
      this.context.createMediaStreamSource(this.stream).connect(this.node);
      this.node.connect(this.context.destination);
      this.recording = true;
      this.timer = setTimeout(/** End recording automatically at the fixed 120-second capture limit. */ () => { void this.end(); }, 120000);
    } catch (error) {
      await this.close();
      throw error;
    }
  }

  /** Flush audio before sending stop, then wait for the provider's final transcript. */ async end() {
    if (!this.recording || this.closed) return;
    this.recording = false;
    clearTimeout(this.timer);
    try {
      await new Promise(/** Wait for the worklet's final PCM to precede the stop message. */ (resolve, reject) => {
        const timeout = setTimeout(/** Fail capture if the worklet cannot flush promptly. */ () => reject(new Error("Microphone flush timed out.")), 2000);
        /** Resolve the flush acknowledgement and clear its timer. */ this.flushResolve = () => { clearTimeout(timeout); resolve(); };
        this.node.port.postMessage("flush");
      });
      await this.releaseAudio();
      if (this.closed) return;
      this.socket.send(JSON.stringify({ type: "stop" }));
      this.finalTimer = setTimeout(/** Report a provider that failed to finalize the ended answer. */ () => this.fail(new Error("Final transcription timed out; start a new recording.")), 15000);
    } catch (error) { this.fail(error); }
  }

  /** Reject startup and release resources after a public error message. */ fail(error) {
    if (this.closed) return;
    this.startReject?.(error);
    this.onError(error.message);
    void this.close();
  }

  /** Stop microphone tracks, detach the worklet and close its audio context. */ async releaseAudio() {
    /** Stop one acquired microphone track. */ this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    this.node?.disconnect();
    this.node = null;
    const context = this.context;
    this.context = null;
    if (context && context.state !== "closed") await context.close();
  }

  /** Invalidate socket callbacks and end the local capture lifecycle. */ async close() {
    this.closed = true;
    this.recording = false;
    clearTimeout(this.timer);
    clearTimeout(this.finalTimer);
    this.startReject?.(new Error("Recording cancelled."));
    this.startReject = null;
    this.socket?.close();
    await this.releaseAudio();
  }
}
