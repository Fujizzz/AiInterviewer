/**
 * @module interview-voice
 * Responsibilities: Coordinate voice playback, real-time subtitles, and final transcript submission after manual or observer-confirmed answer completion; does not compute interview evaluation.
 * Implementation: Isolate late events by epoch and capture identity; automatic termination requires final backend credential before callback to agent.js once.
 * Related Modules: SpeechCapture manages PCM/STT; agent.js provides submission callback and current question's answer eligibility.
 *
 * Declaration Index:
 * - InterviewVoice:
 *   Coordinate subtitles and manual/observer-confirmed answers, isolating stale playback/STT events.
 * - InterviewVoice.constructor:
 *   Store the answer callback and initialize manual/automatic completion and speech state.
 * - InterviewVoice.constructor.callback1:
 *   Connect the avatar when the user clicks its playback button.
 * - InterviewVoice.constructor.callback2:
 *   Replay the current question on explicit request.
 * - InterviewVoice.constructor.callback3:
 *   Interrupt the active question and restore answer controls.
 * - InterviewVoice.constructor.callback4:
 *   Start one microphone recognition session.
 * - InterviewVoice.constructor.callback5:
 *   Confirm answer completion and flush captured PCM before submission.
 * - InterviewVoice.constructor.callback6:
 *   Stop question playback when automatic speech is disabled.
 * - InterviewVoice.constructor.callback7:
 *   Apply the main interview's answer eligibility to voice controls.
 * - InterviewVoice.message:
 *   Show a plain-text presentation status without rendering model output as HTML.
 * - InterviewVoice.updateControls:
 *   Gate capture/end buttons during playback, finalization and pending interview requests.
 * - InterviewVoice.subtitle:
 *   Render plain-text subtitles in the video stage and follow the newest lines.
 * - InterviewVoice.finishAnswer:
 *   Register manual/automatic end confirmation and flush capture; auto requires a final receipt.
 * - InterviewVoice.submitTranscript:
 *   Consume confirmed final text once and call the supplied interview submission boundary.
 * - InterviewVoice.connectAvatar:
 *   Load the official player bundle and connect the local signalling endpoint.
 * - InterviewVoice.connectAvatar.callback1:
 *   Route UE playback events through utterance identity checks.
 * - InterviewVoice.connectAvatar.callback2:
 *   Display connection status supplied by the avatar player.
 * - InterviewVoice.avatarEvent:
 *   Accept current playback events and select voice fallback after a disconnect.
 * - InterviewVoice.avatarEvent.callback1:
 *   Release controls if a current UE playback exceeds its deadline.
 * - InterviewVoice.setQuestion:
 *   Invalidate earlier audio and optionally speak the newly displayed question.
 * - InterviewVoice.setState:
 *   Send presentation state without changing interview scoring or answers.
 * - InterviewVoice.speak:
 *   Request a complete WAV and choose exactly one UE or browser playback path.
 * - InterviewVoice.speak.callback1:
 *   Abort only this TTS request when its deadline expires.
 * - InterviewVoice.speak.callback2:
 *   Cancel stalled UE preparation before switching to browser audio.
 * - InterviewVoice.fallbackAudio:
 *   Play voice-only audio when the avatar cannot render the current question.
 * - InterviewVoice.fallbackAudio.this.audio.onended:
 *   Restore answering after the current browser audio completes.
 * - InterviewVoice.fallbackAudio.this.audio.onerror:
 *   Release controls and report a current audio download failure.
 * - InterviewVoice.fallbackAudio.callback1:
 *   Handle autoplay refusal without automatically retrying synthesis.
 * - InterviewVoice.stopPlayback:
 *   Invalidate pending callbacks and stop both possible audio paths.
 * - InterviewVoice.record:
 *   Capture one voice answer, showing partial and final subtitles without an editable input.
 * - InterviewVoice.record.callback1:
 *   Display draft words only while this capture epoch remains current.
 * - InterviewVoice.record.callback2:
 *   Retain final text; submit after manual confirmation or a valid automatic receipt.
 * - InterviewVoice.record.callback3:
 *   Release recording controls after a current recognition failure.
 * - InterviewVoice.record.object1.onCompletion:
 *   Flush this capture after the independent observer confirms end intent and silence.
 * - InterviewVoice.reset:
 *   Cancel resources/confirmation; optionally preserve submitted subtitles while awaiting the next question.
 * - InterviewVoice.close:
 *   Release capture, playback and streaming resources when leaving the page.
 *
 * Variable Index:
 * None
 */
import { SpeechCapture } from "./speech-capture.js";

/**
 *  Function: Voice and subtitle coordination; Logic: Submit based on epoch, capture identity, and manual/auto confirmation; Constraint: No evaluation or implicit retry.
 */
export class InterviewVoice {
  /**
 *  Input: onAnswer(text, receipt) callback; Output: Coordinator; Called only when non-empty final text is confirmed manually or automatically.
 * Initial state: no question/capture/confirmation; register button and answer eligibility listeners, do not request device or network.
 */ constructor(onAnswer) {
    this.onAnswer = onAnswer;
    this.finalTranscript = null;
    this.finishRequested = false;
    this.autoFinish = false;
    this.completionReceipt = null;
    this.completionEnabled = false;
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
    /** Confirm answer completion and flush captured PCM before submission. */ document.getElementById("stop-recording").onclick = () => { void this.finishAnswer(); };
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

  /**
 *  No parameters; read answer/playback/capture/finish state to update buttons, no network or submission side effects.
 * Disallow repeated finish during cleanup; final text triggered by timeout still requires button confirmation, not auto-submit.
 */ updateControls() {
    const recording = !!this.capture;
    document.getElementById("start-recording").disabled = !this.eligible || this.busy || recording || this.finalTranscript !== null;
    document.getElementById("stop-recording").disabled = !this.eligible || this.busy || this.finishRequested || (!this.capture?.recording && this.finalTranscript === null);
    document.getElementById("replay-question").disabled = !this.eligible || recording || this.finalTranscript !== null;
    document.getElementById("interrupt-speech").disabled = !this.busy;
    document.getElementById("voice-enabled").disabled = recording || this.finalTranscript !== null;
  }

  /**
 *  Input: current transcribed text; Output: none; textContent prevents transcription from being executed as HTML; empty text hides subtitles.
 * Long answers scroll independently within video stage, following latest text; subtitles are not persisted to browser storage.
 */ subtitle(text) {
    const node = document.getElementById("answer-subtitle");
    node.textContent = text;
    document.getElementById("answer-subtitles").hidden = !text;
    node.scrollTop = node.scrollHeight;
  }

  /**
 *  Input: source (default manual); button or backend three-second silence event registers end, then wait for flush/final STT.
 * auto only applies to active capture; final requires receipt; timeout itself does not auto-submit; repeated calls do not generate requests.
 */ async finishAnswer(source = "manual") {
    if (!this.eligible || this.busy || this.finishRequested) return;
    if (source === "auto" && !this.capture?.recording) return;
    if (this.finalTranscript !== null) { this.finishRequested = true; this.submitTranscript(); return; }
    if (!this.capture?.recording) return;
    this.finishRequested = true;
    this.autoFinish = source === "auto";
    console.info("Interview speech answer end confirmed", { epoch: this.epoch, source });
    this.message(this.autoFinish
      ? (window.AppI18n?.t("voice_auto_finalizing") ?? "Answer completion detected. Finalizing and submitting…")
      : (window.AppI18n?.t("voice_finalizing") ?? "Finalizing the transcript and submitting your answer…"));
    this.updateControls();
    await this.capture.end();
  }

  /**
 *  No external parameters; consume confirmed non-empty final transcript and call onAnswer(text), executed only once.
 * Automatic branch provides credential bound to full text; callback after state clear, current question/UUID re-validated by business module.
 */ submitTranscript() {
    if (!this.finishRequested || !this.finalTranscript || !this.eligible || this.busy || this.capture) return;
    const text = this.finalTranscript;
    const receipt = this.autoFinish ? this.completionReceipt : null;
    if (this.autoFinish && !receipt) return;
    this.finalTranscript = null;
    this.finishRequested = false;
    this.autoFinish = false;
    this.completionReceipt = null;
    this.updateControls();
    console.info("Interview speech answer ready for submission", { textLength: text.length });
    this.onAnswer(text, receipt);
  }

  /** Load the official player bundle and connect the local signalling endpoint. */ async connectAvatar() {
    try {
      if (!this.player) {
        const { AvatarPlayer } = await import("/stream-demo/pixel-player.js");
        this.player = new AvatarPlayer(document.getElementById("avatar-view"),
          /** Route UE playback events through utterance identity checks. */ (event) => this.avatarEvent(event), /** Display connection status supplied by the avatar player. */ (text) => this.message(text));
      }
      this.player.connect(document.getElementById("signalling-url").value.trim());
    } catch (error) { this.message(window.AppI18n?.t("voice_avatar_unavailable", { message: error.message }) ?? `The avatar is unavailable: ${error.message}. You can continue the voice interview.`); }
  }

  /** Accept current playback events and select voice fallback after a disconnect. */ avatarEvent(event) {
    if (event.type === "avatar_ready") { this.setState(this.state); return; }
    if (event.type === "avatar_stats") {
      document.getElementById("avatar-metrics").textContent = window.AppI18n?.t("voice_avatar_fps", { fps: event.fps.toFixed(1) }) ?? `Stream ${event.fps.toFixed(1)} FPS`;
      return;
    }
    if (event.type === "avatar_disconnected") {
      if (this.busy) {
        if (this.playbackStarted) { this.stopPlayback(); this.message(window.AppI18n?.t("voice_avatar_disconnected") ?? "The avatar disconnected. The question is visible; replay it or answer directly."); }
        else if (this.audioUrl) { this.fallbackAudio(this.audioUrl, this.epoch); }
      }
      return;
    }
    if (!this.utteranceId || event.utterance_id !== this.utteranceId) return;
    if (event.type === "playback_started") {
      if (!this.playbackStarted && this.avatarRequestedAt !== null) {
        document.getElementById("speech-metrics").textContent += ` · ${(window.AppI18n?.t("voice_avatar_prepare", { ms: Math.round(performance.now() - this.avatarRequestedAt) }) ?? `UE preparation ${Math.round(performance.now() - this.avatarRequestedAt)} ms`)}`;
      }
      this.playbackStarted = true;
      this.state = "speaking";
      clearTimeout(this.playbackTimer);
      this.playbackTimer = setTimeout(/** Release controls if a current UE playback exceeds its deadline. */ () => { this.stopPlayback(); this.message(window.AppI18n?.t("voice_avatar_timeout") ?? "Avatar playback timed out. You can answer directly."); }, 125000);
      this.message(window.AppI18n?.t("voice_avatar_speaking") ?? "The interviewer is speaking…");
    }
    if (event.type === "playback_finished" || event.type === "interrupted") this.stopPlayback();
    if (event.type === "playback_failed") {
      this.message(window.AppI18n?.t("voice_avatar_playback_failed", { message: event.detail }) ?? `Avatar playback failed: ${event.detail}. Switching to audio.`);
      this.fallbackAudio(this.audioUrl, this.epoch);
    }
  }

  /** Invalidate earlier audio and optionally speak the newly displayed question. */ setQuestion(question, backendWaitMs = null) {
    this.reset();
    this.question = question;
    this.backendWaitMs = backendWaitMs;
    document.getElementById("speech-metrics").textContent = backendWaitMs === null ? "" : (window.AppI18n?.t("voice_question_wait", { ms: Math.round(backendWaitMs) }) ?? `Question wait ${Math.round(backendWaitMs)} ms`);
    if (document.getElementById("voice-enabled").checked) void this.speak();
  }

  /** Send presentation state without changing interview scoring or answers. */ setState(state) { this.state = state; this.player?.send({ type: "state", state }); }

  /** Request a complete WAV and choose exactly one UE or browser playback path. */ async speak() {
    if (!this.question || this.capture) return;
    this.stopPlayback();
    const epoch = ++this.epoch;
    this.busy = true;
    this.updateControls();
    this.message(window.AppI18n?.t("voice_question_generating") ?? "Generating English speech…");
    this.setState("thinking");
    const started = performance.now();
    const abort = new AbortController();
    this.abort = abort;
    const timeout = setTimeout(/** Abort only this TTS request when its deadline expires. */ () => abort.abort(), 50000);
    try {
      const headers = { "Content-Type": "application/json" };
      const csrf = document.getElementById("csrf-token")?.content;
      if (csrf) headers["X-CSRFToken"] = csrf;
      const response = await fetch("/api/speech/tts/", {
        method: "POST", headers,
        body: JSON.stringify({ text: this.question.text }), signal: this.abort.signal,
      });
      const result = await response.json();
      if (epoch !== this.epoch) return;
      if (!response.ok) throw new Error(`${result.error?.code}: ${result.error?.detail}`);
      this.utteranceId = result.utterance_id;
      this.audioUrl = result.audio_url;
      document.getElementById("speech-metrics").textContent = `${this.backendWaitMs === null ? "" : `${window.AppI18n?.t("voice_question_wait", { ms: Math.round(this.backendWaitMs) }) ?? `Question wait ${Math.round(this.backendWaitMs)} ms`} · `}${window.AppI18n?.t("voice_tts_metrics", { generation: result.generation_ms, request: Math.round(performance.now() - started) }) ?? `TTS ${result.generation_ms} ms · speech request ${Math.round(performance.now() - started)} ms`}`;
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
        this.message(window.AppI18n?.t("voice_question_unavailable", { message: error.message }) ?? `Question playback is unavailable: ${error.message}. Read the question and answer by voice.`);
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
    /** Release controls and report a current audio download failure. */ this.audio.onerror = () => { if (epoch === this.epoch) { this.stopPlayback(); this.message(window.AppI18n?.t("voice_audio_load_failed") ?? "Question audio failed to load. Read the question and answer by voice."); } };
    this.message(window.AppI18n?.t("voice_mode_reading") ?? "Voice mode: reading the question…");
    /** Handle autoplay refusal without automatically retrying synthesis. */ this.audio.play().catch(() => {
      if (epoch === this.epoch) { this.stopPlayback(); this.message(window.AppI18n?.t("voice_enable_audio") ?? "Select Replay question to enable audio, or answer directly."); }
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
    if (this.question) this.message(window.AppI18n?.t("voice_listening") ?? "Speech has stopped. Click Start answering to record your voice.");
    this.updateControls();
  }

  /**
 *  Start capture when current question is answerable and playback ends; real-time subtitles are not submission content.
 * Final text validated by epoch/capture identity; only manual confirmation or valid automatic receipt allows submission; failure does not record body.
 * Default capture timeout/encoding/vendor timeout preserved; no text input or auto-re-recording introduced.
 */ async record() {
    if (!this.eligible || this.busy || this.capture || this.finalTranscript !== null) return;
    this.stopPlayback();
    const epoch = this.epoch;
    const started = performance.now();
    this.finishRequested = false;
    this.autoFinish = false;
    this.completionReceipt = null;
    this.subtitle("");
    const capture = new SpeechCapture(
      /** Display draft words only while this capture epoch remains current. */ (text) => { if (epoch === this.epoch && this.capture === capture) this.subtitle(text); },
      /**
 *  Preserve current final text; missing credential in automatic branch indicates supplement invalidates detection, keeps pending manual confirmation, no auto-submit.
 */ (text, finalizationMs, receipt) => {
        if (epoch !== this.epoch || this.capture !== capture) return;
        this.capture = null;
        this.finalTranscript = text.trim() || null;
        this.completionReceipt = receipt ?? null;
        const revoked = this.autoFinish && !receipt;
        if (this.autoFinish && !receipt) {
          this.finishRequested = false;
          this.autoFinish = false;
        }
        console.info("Interview final speech received", { endRequested: this.finishRequested, textLength: this.finalTranscript?.length ?? 0 });
        this.subtitle(this.finalTranscript ?? "");
        if (!this.finalTranscript) {
          this.finishRequested = false;
          this.message(window.AppI18n?.t("voice_empty") ?? "No speech was recognized. Click Start answering to record again.");
        } else if (!this.finishRequested) {
          this.message(revoked
            ? (window.AppI18n?.t("voice_auto_revoked") ?? "Additional speech cancelled automatic submission. Review the subtitles and click Finish answer.")
            : (window.AppI18n?.t("voice_final_ready") ?? "Recording has ended. Click Finish answer to submit the subtitled answer."));
        }
        document.getElementById("speech-metrics").textContent += ` · ${(window.AppI18n?.t("voice_recording_metrics", { recording: Math.round(performance.now() - started), finalization: finalizationMs ?? "—" }) ?? `Recording with transcript ${Math.round(performance.now() - started)} ms · STT finalization ${finalizationMs ?? "—"} ms`)}`;
        this.updateControls();
        this.submitTranscript();
      },
      /** Release recording controls after a current recognition failure. */ (text) => {
        if (epoch !== this.epoch || this.capture !== capture) return;
        console.error("Interview speech recognition failed", { phase: "capture_or_finalize", endRequested: this.finishRequested });
        this.capture = null; this.finishRequested = false; this.finalTranscript = null;
        this.autoFinish = false; this.completionReceipt = null;
        this.message(text); this.updateControls();
      },
      { questionId: this.completionEnabled ? this.question.question_id : null,
        /**
 *  Only initiate automatic closure when current question, epoch, and capture identity match; old events from next question have no side effects.
 */ onCompletion: () => {
          if (epoch === this.epoch && this.capture === capture) void this.finishAnswer("auto");
        },
      },
    );
    this.capture = capture;
    this.message(this.completionEnabled
      ? (window.AppI18n?.t("voice_auto_starting") ?? "Starting microphone and Chinese/English recognition…")
      : (window.AppI18n?.t("voice_starting") ?? "Opening the microphone and English speech recognition…"));
    this.updateControls();
    try {
      await capture.start();
      if (epoch !== this.epoch || capture.closed || this.capture !== capture) { await capture.close(); return; }
      console.info("Interview speech capture started", { epoch });
      this.message(this.completionEnabled
        ? (window.AppI18n?.t("voice_auto_recording") ?? "Recording. Say “I’m done” and pause for 3 seconds to submit automatically, or click Finish answer.")
        : (window.AppI18n?.t("voice_recording") ?? "Recording. Click Finish answer when you are done to submit."));
      this.setState("listening");
      this.updateControls();
      document.getElementById("stop-recording").focus();
    } catch (error) {
      if (epoch === this.epoch && this.capture === capture) {
        console.error("Interview microphone startup failed", { name: error.name });
        this.capture = null; this.finishRequested = false;
        this.message(error.message); this.updateControls();
      }
    }
  }

  /**
 *  Input: clearSubtitle (default true); Output: none; release capture/playback and invalidate confirmation and late callbacks.
 * agent.js submits with false to retain final subtitles; other new question/cancel/page leave paths clear subtitles; does not alter business state.
 */ reset(clearSubtitle = true) {
    this.stopPlayback();
    void this.capture?.close();
    this.capture = null;
    this.question = null;
    this.finalTranscript = null;
    this.finishRequested = false;
    this.autoFinish = false;
    this.completionReceipt = null;
    if (clearSubtitle) this.subtitle("");
    this.updateControls();
  }

  /** Release capture, playback and streaming resources when leaving the page. */ close() { this.reset(); this.setState("idle"); this.player?.close(); }
}
