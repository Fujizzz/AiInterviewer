/**
 * @module interview-voice
 * Local speech presentation; no interview evaluation or automatic answer submission.
 *
 * 目录：
 * - InterviewVoice：
 *   Presentation coordinator; epochs isolate stale TTS, playback and STT events.
 * - InterviewVoice.constructor：
 *   Initialize presentation state and connect the manual interview controls.
 * - InterviewVoice.constructor.callback1：
 *   Connect the avatar when the user clicks its playback button.
 * - InterviewVoice.constructor.callback2：
 *   Replay the current question on explicit request.
 * - InterviewVoice.constructor.callback3：
 *   Interrupt the active question and restore answer controls.
 * - InterviewVoice.constructor.callback4：
 *   Start one microphone recognition session.
 * - InterviewVoice.constructor.callback5：
 *   Flush captured PCM and end the current answer.
 * - InterviewVoice.constructor.callback6：
 *   Stop question playback when automatic speech is disabled.
 * - InterviewVoice.constructor.callback7：
 *   Apply the main interview's answer eligibility to voice controls.
 * - InterviewVoice.message：
 *   Show a plain-text presentation status without rendering model output as HTML.
 * - InterviewVoice.updateControls：
 *   Prevent submission during playback, recording or an unfinished agent request.
 * - InterviewVoice.connectAvatar：
 *   Load the official player bundle and connect the local signalling endpoint.
 * - InterviewVoice.connectAvatar.callback1：
 *   Route UE playback events through utterance identity checks.
 * - InterviewVoice.connectAvatar.callback2：
 *   Display connection status supplied by the avatar player.
 * - InterviewVoice.avatarEvent：
 *   Accept current playback events and select voice fallback after a disconnect.
 * - InterviewVoice.avatarEvent.callback1：
 *   Release controls if a current UE playback exceeds its deadline.
 * - InterviewVoice.setQuestion：
 *   Invalidate earlier audio and optionally speak the newly displayed question.
 * - InterviewVoice.setState：
 *   Send presentation state without changing interview scoring or answers.
 * - InterviewVoice.speak：
 *   Request a complete WAV and choose exactly one UE or browser playback path.
 * - InterviewVoice.speak.callback1：
 *   Abort only this TTS request when its deadline expires.
 * - InterviewVoice.speak.callback2：
 *   Cancel stalled UE preparation before switching to browser audio.
 * - InterviewVoice.fallbackAudio：
 *   Play voice-only audio when the avatar cannot render the current question.
 * - InterviewVoice.fallbackAudio.this.audio.onended：
 *   Restore answering after the current browser audio completes.
 * - InterviewVoice.fallbackAudio.this.audio.onerror：
 *   Release controls and report a current audio download failure.
 * - InterviewVoice.fallbackAudio.callback1：
 *   Handle autoplay refusal without automatically retrying synthesis.
 * - InterviewVoice.stopPlayback：
 *   Invalidate pending callbacks and stop both possible audio paths.
 * - InterviewVoice.record：
 *   Capture one answer and fill an editable transcript without submitting it.
 * - InterviewVoice.record.callback1：
 *   Display draft words only while this capture epoch remains current.
 * - InterviewVoice.record.callback2：
 *   Fill confirmed text and display STT finalization timing.
 * - InterviewVoice.record.callback3：
 *   Release recording controls after a current recognition failure.
 * - InterviewVoice.reset：
 *   Cancel speech resources and clear only presentation state for the next question.
 * - InterviewVoice.close：
 *   Release capture, playback and streaming resources when leaving the page.
 *
 * 关键变量：
 * （无模块级变量。）
 */
import { SpeechCapture } from "./speech-capture.js";

/** Presentation coordinator; epochs isolate stale TTS, playback and STT events. */
export class InterviewVoice {
  /** Initialize presentation state and connect the manual interview controls. */ constructor() {
    this.epoch = 0;
    this.question = null;
    this.eligible = false;
    this.busy = false;
    this.capture = null;
    this.player = null;
    this.audio = null;
    this.abort = null;
    this.utteranceId = null;
    this.playbackTimer = null;
    this.playbackStarted = false;
    this.audioUrl = null;
    this.state = "idle";
    this.backendWaitMs = null;
    this.avatarRequestedAt = null;
    /** Connect the avatar when the user clicks its playback button. */ document.getElementById("connect-avatar").onclick = () => { void this.connectAvatar(); };
    /** Replay the current question on explicit request. */ document.getElementById("replay-question").onclick = () => { void this.speak(); };
    /** Interrupt the active question and restore answer controls. */ document.getElementById("interrupt-speech").onclick = () => this.stopPlayback();
    /** Start one microphone recognition session. */ document.getElementById("start-recording").onclick = () => { void this.record(); };
    /** Flush captured PCM and end the current answer. */ document.getElementById("stop-recording").onclick = () => { void this.capture?.end(); };
    /** Stop question playback when automatic speech is disabled. */ document.getElementById("voice-enabled").onchange = () => {
      if (!document.getElementById("voice-enabled").checked) this.stopPlayback();
    };
    /** Apply the main interview's answer eligibility to voice controls. */ window.addEventListener("interview-controls", ({ detail }) => {
      this.eligible = detail.answering;
      this.updateControls();
    });
    this.updateControls();
  }

  /** Show a plain-text presentation status without rendering model output as HTML. */ message(text) { document.getElementById("voice-status").textContent = text; }

  /** Prevent submission during playback, recording or an unfinished agent request. */ updateControls() {
    const recording = !!this.capture;
    document.getElementById("start-recording").disabled = !this.eligible || this.busy || recording;
    document.getElementById("stop-recording").disabled = !this.capture?.recording;
    document.getElementById("replay-question").disabled = !this.eligible || recording;
    document.getElementById("interrupt-speech").disabled = !this.busy;
    document.getElementById("submit-answer").disabled = !this.eligible || this.busy || recording;
    document.getElementById("answer").disabled = !this.eligible || recording;
  }

  /** Load the official player bundle and connect the local signalling endpoint. */ async connectAvatar() {
    try {
      if (!this.player) {
        const { AvatarPlayer } = await import("/stream-demo/pixel-player.js");
        this.player = new AvatarPlayer(document.getElementById("avatar-view"),
          /** Route UE playback events through utterance identity checks. */ (event) => this.avatarEvent(event), /** Display connection status supplied by the avatar player. */ (text) => this.message(text));
      }
      this.player.connect(document.getElementById("signalling-url").value.trim());
    } catch (error) { this.message(`数字人不可用：${error.message}；可继续语音或文字面试。`); }
  }

  /** Accept current playback events and select voice fallback after a disconnect. */ avatarEvent(event) {
    if (event.type === "avatar_ready") { this.setState(this.state); return; }
    if (event.type === "avatar_stats") {
      document.getElementById("avatar-metrics").textContent = `串流 ${event.fps.toFixed(1)} FPS`;
      return;
    }
    if (event.type === "avatar_disconnected") {
      if (this.busy) {
        if (this.playbackStarted) { this.stopPlayback(); this.message("数字人断开，问题已显示；可重新朗读或直接回答。"); }
        else if (this.audioUrl) { this.fallbackAudio(this.audioUrl, this.epoch); }
      }
      return;
    }
    if (!this.utteranceId || event.utterance_id !== this.utteranceId) return;
    if (event.type === "playback_started") {
      if (!this.playbackStarted && this.avatarRequestedAt !== null) {
        document.getElementById("speech-metrics").textContent += ` · UE 准备 ${Math.round(performance.now() - this.avatarRequestedAt)} ms`;
      }
      this.playbackStarted = true;
      this.state = "speaking";
      clearTimeout(this.playbackTimer);
      this.playbackTimer = setTimeout(/** Release controls if a current UE playback exceeds its deadline. */ () => { this.stopPlayback(); this.message("数字人播放超时，可直接回答。"); }, 125000);
      this.message("面试官正在朗读…");
    }
    if (event.type === "playback_finished" || event.type === "interrupted") this.stopPlayback();
    if (event.type === "playback_failed") {
      this.message(`数字人播放失败：${event.detail}；切换到音频。`);
      this.fallbackAudio(this.audioUrl, this.epoch);
    }
  }

  /** Invalidate earlier audio and optionally speak the newly displayed question. */ setQuestion(question, backendWaitMs = null) {
    this.reset();
    this.question = question;
    this.backendWaitMs = backendWaitMs;
    document.getElementById("speech-metrics").textContent = backendWaitMs === null ? "" : `问题等待 ${Math.round(backendWaitMs)} ms`;
    if (document.getElementById("voice-enabled").checked) void this.speak();
  }

  /** Send presentation state without changing interview scoring or answers. */ setState(state) { this.state = state; this.player?.send({ type: "state", state }); }

  /** Request a complete WAV and choose exactly one UE or browser playback path. */ async speak() {
    if (!this.question || this.capture) return;
    this.stopPlayback();
    const epoch = ++this.epoch;
    this.busy = true;
    this.updateControls();
    this.message("正在生成英文语音…");
    this.setState("thinking");
    const started = performance.now();
    const abort = new AbortController();
    this.abort = abort;
    const timeout = setTimeout(/** Abort only this TTS request when its deadline expires. */ () => abort.abort(), 50000);
    try {
      const response = await fetch("/api/speech/tts/", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: this.question.text }), signal: this.abort.signal,
      });
      const result = await response.json();
      if (epoch !== this.epoch) return;
      if (!response.ok) throw new Error(`${result.error?.code}: ${result.error?.detail}`);
      this.utteranceId = result.utterance_id;
      this.audioUrl = result.audio_url;
      document.getElementById("speech-metrics").textContent = `${this.backendWaitMs === null ? "" : `问题等待 ${Math.round(this.backendWaitMs)} ms · `}TTS ${result.generation_ms} ms · 语音请求 ${Math.round(performance.now() - started)} ms`;
      this.avatarRequestedAt = performance.now();
      if (!this.player?.send({ type: "speak", utterance_id: result.utterance_id, audio_url: result.audio_url })) {
        this.fallbackAudio(result.audio_url, epoch);
      } else {
        this.playbackTimer = setTimeout(/** Cancel stalled UE preparation before switching to browser audio. */ () => {
          this.player?.send({ type: "stop" });
          this.fallbackAudio(result.audio_url, epoch);
        }, 18000);
      }
    } catch (error) {
      if (epoch === this.epoch) {
        this.busy = false;
        this.setState("listening");
        this.message(`语音不可用：${error.message}；可直接输入回答。`);
        this.updateControls();
      }
    } finally { clearTimeout(timeout); }
  }

  /** Play voice-only audio when the avatar cannot render the current question. */ fallbackAudio(url, epoch) {
    clearTimeout(this.playbackTimer);
    if (epoch !== this.epoch || !url) return;
    this.audio?.pause();
    this.utteranceId = null;
    this.audio = new Audio(url);
    /** Restore answering after the current browser audio completes. */ this.audio.onended = () => { if (epoch === this.epoch) this.stopPlayback(); };
    /** Release controls and report a current audio download failure. */ this.audio.onerror = () => { if (epoch === this.epoch) { this.stopPlayback(); this.message("音频加载失败，可直接输入回答。"); } };
    this.message("语音模式：正在朗读问题…");
    /** Handle autoplay refusal without automatically retrying synthesis. */ this.audio.play().catch(() => {
      if (epoch === this.epoch) { this.stopPlayback(); this.message("点击“重新朗读”启用声音，或直接回答。"); }
    });
  }

  /** Invalidate pending callbacks and stop both possible audio paths. */ stopPlayback() {
    ++this.epoch;
    this.abort?.abort();
    this.abort = null;
    clearTimeout(this.playbackTimer);
    this.utteranceId = null;
    this.audioUrl = null;
    this.playbackStarted = false;
    this.avatarRequestedAt = null;
    this.audio?.pause();
    this.audio = null;
    this.player?.send({ type: "stop" });
    this.busy = false;
    this.setState("listening");
    this.updateControls();
  }

  /** Capture one answer and fill an editable transcript without submitting it. */ async record() {
    if (!this.eligible || this.capture) return;
    this.stopPlayback();
    const epoch = this.epoch;
    const started = performance.now();
    const capture = new SpeechCapture(
      /** Display draft words only while this capture epoch remains current. */ (text) => { if (epoch === this.epoch) document.getElementById("transcript-draft").textContent = text; },
      /** Fill confirmed text and display STT finalization timing. */ (text, finalizationMs) => {
        if (epoch !== this.epoch) return;
        document.getElementById("answer").value = text;
        document.getElementById("transcript-draft").textContent = "";
        this.capture = null;
        this.message(text ? "转录已完成，请检查或修改后点击确认提交。" : "未识别到语音，请重试或输入回答。");
        document.getElementById("speech-metrics").textContent += ` · 录音含转录 ${Math.round(performance.now() - started)} ms · STT 收尾 ${finalizationMs ?? "—"} ms`;
        this.updateControls();
      },
      /** Release recording controls after a current recognition failure. */ (text) => { if (epoch === this.epoch) { this.capture = null; this.message(text); this.updateControls(); } },
    );
    this.capture = capture;
    this.message("正在开启麦克风与英文识别…");
    this.updateControls();
    try {
      await capture.start();
      if (epoch !== this.epoch || capture.closed) { await capture.close(); return; }
      this.message("正在录音，回答结束后点击“结束回答”。");
      this.setState("listening");
      this.updateControls();
    } catch (error) {
      if (epoch === this.epoch) { this.capture = null; this.message(error.message); this.updateControls(); }
    }
  }

  /** Cancel speech resources and clear only presentation state for the next question. */ reset() {
    this.stopPlayback();
    void this.capture?.close();
    this.capture = null;
    this.question = null;
    document.getElementById("transcript-draft").textContent = "";
    this.updateControls();
  }

  /** Release capture, playback and streaming resources when leaving the page. */ close() { this.reset(); this.setState("idle"); this.player?.close(); }
}
