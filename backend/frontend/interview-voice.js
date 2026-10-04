/**
 * @module interview-voice
 * Responsibilities: Coordinate playback/subtitles, 15-second preparation, automatic capture and five-second inactivity closure; never compute interview evaluation.
 * Implementation: Isolate late events by epoch/capture identity; flush complete STT before automatic submission. Semantic receipts use MCP; inactivity/capture-limit closures use the normal answer boundary. Explicit end suspends submission.
 * Related Modules: SpeechCapture manages PCM/STT; agent.js provides submission callback and current question's answer eligibility.
 *
 * Declaration Index:
 * - InterviewVoice: Functionality: Coordinate automatic answering and safe final transcripts. Logic: Bind clocks/callbacks to the current capture and suspend during early-end choices. Constraints: No evaluation or capture retry.
 * - InterviewVoice.constructor: Inputs: onAnswer(text, receipt) callback. Outputs: Coordinator; callback receives full final text, including empty text for explicit unanswered closure, with optional semantic receipt.
 * - InterviewVoice.constructor.callback1: Connect the avatar when the user clicks its playback button.
 * - InterviewVoice.constructor.callback2: Replay the current question on explicit request.
 * - InterviewVoice.constructor.callback3: Interrupt the active question and restore answer controls.
 * - InterviewVoice.constructor.callback4: Stop question playback when automatic speech is disabled.
 * - InterviewVoice.constructor.callback5: Apply the main interview's answer eligibility to voice controls.
 * - InterviewVoice.message: Show a plain-text presentation status without rendering model output as HTML.
 * - InterviewVoice.clearAnswerTimer: Functionality: Stop the current preparation/inactivity clock; no device or interview mutation.
 * - InterviewVoice.countdown: Functionality: Start a visible monotonic preparation or inactivity countdown.
 * - InterviewVoice.countdown.callback1: Update the current clock only.
 * - InterviewVoice.tick: Functionality: Render remaining seconds and perform one automatic phase transition.
 * - InterviewVoice.activity: Functionality: Refresh five silent seconds after voiced PCM or changed transcription.
 * - InterviewVoice.takeFinalAnswer: Functionality: Flush the current answer for an explicit early-end report.
 * - InterviewVoice.takeFinalAnswer.callback1: Save finalization callbacks for this capture.
 * - InterviewVoice.resumeAnswer: Functionality: Resume an interview after dismissing the end dialog.
 * - InterviewVoice.updateControls: No parameters; read answer/playback/capture/finish state to update buttons, no network or submission side effects.
 * - InterviewVoice.subtitle: Input: current transcribed text; Output: none; textContent prevents transcription from being executed as HTML; empty text hides subtitles.
 * - InterviewVoice.finishAnswer: Inputs: source (default silence); five-second inactivity or backend completion closes capture. Outputs: None.
 * - InterviewVoice.submitTranscript: Inputs: Instance final transcript, eligibility and suspended state. Outputs: None.
 * - InterviewVoice.connectAvatar: Load the official player bundle and connect the local signalling endpoint.
 * - InterviewVoice.connectAvatar.callback1: Display connection status supplied by the avatar player.
 * - InterviewVoice.connectAvatar.callback2: Display connection status supplied by the avatar player.
 * - InterviewVoice.avatarEvent: Accept current playback events and select voice fallback after a disconnect.
 * - InterviewVoice.avatarEvent.callback1: Release controls if a current UE playback exceeds its deadline.
 * - InterviewVoice.setQuestion: Inputs: Current question and optional backend wait latency. Outputs: None. Logic: Reset stale resources, start 15-second preparation, then optionally speak. Constraints: Countdown starts at question display and stops playback at expiry.
 * - InterviewVoice.setState: Send presentation state without changing interview scoring or answers.
 * - InterviewVoice.speak: Request a complete WAV and choose exactly one UE or browser playback path.
 * - InterviewVoice.speak.callback1: Abort only this TTS request when its deadline expires.
 * - InterviewVoice.speak.callback2: Cancel stalled UE preparation before switching to browser audio.
 * - InterviewVoice.fallbackAudio: Play voice-only audio when the avatar cannot render the current question.
 * - InterviewVoice.fallbackAudio.this.audio.onended: Restore answering after the current browser audio completes.
 * - InterviewVoice.fallbackAudio.this.audio.onerror: Release controls and report a current audio download failure.
 * - InterviewVoice.fallbackAudio.callback1: Handle autoplay refusal without automatically retrying synthesis.
 * - InterviewVoice.stopPlayback: Invalidate pending callbacks and stop both possible audio paths.
 * - InterviewVoice.record: Functionality: Start capture automatically after preparation. Inputs: Current question/eligibility/playback state. Outputs: None.
 * - InterviewVoice.record.callback1: Refresh activity only for changed current text and show safe subtitles.
 * - InterviewVoice.record.callback2: Inputs: Full final provider text, finalization latency and optional receipt. Outputs: None. Logic: Stop clock, resolve explicit-end waiter or submit once; absent receipt selects the normal automatic answer path. Constraints: No partial-text substitution or invented response.
 * - InterviewVoice.record.callback3: Release recording controls after a current recognition failure.
 * - InterviewVoice.record.object1.onActivity: Only voiced PCM belonging to this capture refreshes the silence deadline.
 * - InterviewVoice.record.object1.onCompletion: Only initiate automatic closure when current question, epoch, and capture identity match; old events from next question have no side effects.
 * - InterviewVoice.reset: Input: clearSubtitle (default true); Output: none; release capture/playback and invalidate confirmation and late callbacks.
 * - InterviewVoice.close: Release capture, playback and streaming resources when leaving the page.
 * Variable Index:
 * None
 */
import { SpeechCapture } from "./speech-capture.js";

/**
 *  Functionality: Coordinate automatic answering and safe final transcripts. Logic: Bind clocks/callbacks to the current capture and suspend during early-end choices. Constraints: No evaluation or capture retry.
 */
export class InterviewVoice {
  /**
 *  Inputs: onAnswer(text, receipt) callback. Outputs: Coordinator; callback receives full final text, including empty text for explicit unanswered closure, with optional semantic receipt.
 * Initial state: no question/capture/confirmation; register playback and answer eligibility listeners without requesting device or network. State includes phase/deadline/answerTimer, suspended end choice, and finalWaiter for an explicit report flush.
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
    this.answerTimer = null;
    this.phase = "idle";
    this.deadline = null;
    this.suspended = false;
    this.finalWaiter = null;
    /** Connect the avatar when the user clicks its playback button. */ document.getElementById("connect-avatar").onclick = () => { void this.connectAvatar(); };
    /** Replay the current question on explicit request. */ document.getElementById("replay-question").onclick = () => { void this.speak(); };
    /** Interrupt the active question and restore answer controls. */ document.getElementById("interrupt-speech").onclick = () => this.stopPlayback();
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

  /** Functionality: Stop the current preparation/inactivity clock; no device or interview mutation.
   * Inputs: Instance timer. Outputs: None. Logic: Clear interval and deadline atomically.
   * Constraints: Late ticks see no phase deadline; capture cleanup remains owned by reset.
   */ clearAnswerTimer() {
    clearInterval(this.answerTimer);
    this.answerTimer = null;
    this.deadline = null;
  }

  /** Functionality: Start a visible monotonic preparation or inactivity countdown.
   * Inputs: phase and seconds. Outputs: None. Logic: Replace clock and tick immediately.
   * Constraints: Only the current question's clock is active; no replay/retry of failed capture.
   */ countdown(phase, seconds) {
    this.clearAnswerTimer();
    this.phase = phase;
    this.deadline = performance.now() + seconds * 1000;
    this.answerTimer = setInterval(/** Update the current clock only. */ () => this.tick(), 100);
    this.tick();
  }

  /** Functionality: Render remaining seconds and perform one automatic phase transition.
   * Inputs: Current question, eligibility, phase, deadline and suspension state.
   * Outputs: None. Logic: Preparation expiry stops playback before opening the microphone;
   * inactivity expiry flushes recognition. A cleared deadline prevents duplicate transitions.
   * Constraints: Microphone startup/recognition failures remain explicit and require user action.
   */ tick() {
    const node = document.getElementById("answer-countdown");
    if (this.deadline === null || this.suspended) return;
    const seconds = Math.max(0, Math.ceil((this.deadline - performance.now()) / 1000));
    const key = this.phase === "preparing" ? "voice_prepare_countdown" : "voice_silence_countdown";
    node.hidden = false;
    node.textContent = window.AppI18n?.t(key, { seconds }) ?? `${this.phase}: ${seconds}s`;
    if (seconds > 0 || !this.eligible) return;
    const phase = this.phase;
    this.clearAnswerTimer();
    if (phase === "preparing") { this.stopPlayback(); void this.record(); }
    else if (phase === "answering") void this.finishAnswer("silence");
  }

  /** Functionality: Refresh five silent seconds after voiced PCM or changed transcription.
   * Inputs: Current capture state. Outputs: None. Logic: Move deadline without spawning timers.
   * Constraints: Does not treat silent PCM or unchanged provider text as speech.
   */ activity() {
    if (this.phase === "answering" && this.deadline !== null && !this.finishRequested) this.deadline = performance.now() + 5000;
  }

  /** Functionality: Flush the current answer for an explicit early-end report.
   * Inputs: Instance capture/final transcript. Outputs: Promise of final text or startup/STT error.
   * Logic: Suspend automatic submission, await provider finalization once, then hand text to agent.js.
   * Constraints: Partial subtitles are never passed as a final answer; empty text remains unscored.
   */ async takeFinalAnswer() {
    this.suspended = true;
    this.clearAnswerTimer();
    if (!this.capture) { this.stopPlayback(); return this.finalTranscript ?? ""; }
    if (!this.capture.recording && this.phase !== "finalizing") throw new Error(window.AppI18n?.t("voice_start_pending") ?? "Microphone is still starting. Wait before ending with evaluation.");
    const result = new Promise(/** Save finalization callbacks for this capture. */ (resolve, reject) => { this.finalWaiter = { resolve, reject }; });
    this.finishRequested = true;
    if (this.capture.recording) await this.capture.end();
    return result;
  }

  /** Functionality: Resume an interview after dismissing the end dialog.
   * Inputs: Current capture/question. Outputs: None. Logic: Restore a five-second speech clock
   * or remaining preparation duration; completed capture submits its final transcript once.
   * Constraints: Does not open another microphone after a failed capture.
   */ resumeAnswer() {
    this.suspended = false;
    if (this.capture?.recording) this.countdown("answering", 5);
    else if (this.phase === "preparing") this.tick();
    else this.submitTranscript();
  }

  /**
 *  No parameters; read answer/playback/capture/finish state to update buttons, no network or submission side effects.
 * Answer start/end are automatic; only playback/device controls are exposed.
 */ updateControls() {
    const recording = !!this.capture;
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
 *  Inputs: source (default silence); five-second inactivity or backend completion closes capture. Outputs: None.
 * Logic: Register one end, stop the clock and flush PCM before waiting for final STT. Constraints: Suspended/stale/repeated closure has no effect; semantic receipt is forwarded only when final words retain it.
 */ async finishAnswer(source = "silence") {
    if (!this.eligible || this.busy || this.finishRequested || this.suspended) return;
    if (source === "auto" && !this.capture?.recording) return;
    if (this.finalTranscript !== null) { this.finishRequested = true; this.submitTranscript(); return; }
    if (!this.capture?.recording) return;
    this.finishRequested = true;
    this.clearAnswerTimer();
    this.phase = "finalizing";
    document.getElementById("answer-countdown").textContent = window.AppI18n?.t("voice_finalizing") ?? "Finalizing answer…";
    this.autoFinish = source === "auto";
    console.info("Interview speech answer end confirmed", { epoch: this.epoch, source });
    this.message(this.autoFinish
      ? (window.AppI18n?.t("voice_auto_finalizing") ?? "Answer completion detected. Finalizing and submitting…")
      : (window.AppI18n?.t("voice_finalizing") ?? "Finalizing the transcript and submitting your answer…"));
    this.updateControls();
    await this.capture.end();
  }

  /**
 *  Inputs: Instance final transcript, eligibility and suspended state. Outputs: None.
 * Logic: Consume final text once (empty means unanswered), attach valid semantic receipt when present, then clear local confirmation before calling onAnswer. Constraints: No partial captions; agent.js revalidates current question and request eligibility.
 */ submitTranscript() {
    if (!this.finishRequested || this.finalTranscript === null || !this.eligible || this.busy || this.capture || this.suspended) return;
    const text = this.finalTranscript;
    const receipt = this.autoFinish ? this.completionReceipt : null;
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

  /** Inputs: Current question and optional backend wait latency. Outputs: None. Logic: Reset stale resources, start 15-second preparation, then optionally speak. Constraints: Countdown starts at question display and stops playback at expiry. */ setQuestion(question, backendWaitMs = null) {
    this.reset();
    this.question = question;
    this.suspended = false;
    this.countdown("preparing", 15);
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
    if (this.question) this.message(window.AppI18n?.t("voice_listening") ?? "The microphone starts automatically after preparation.");
    this.updateControls();
  }

  /**
 *  Functionality: Start capture automatically after preparation. Inputs: Current question/eligibility/playback state. Outputs: None.
 * Logic: Bind callbacks to epoch/capture identity, refresh inactivity only for voiced PCM or changed text, and submit full final text once. Constraints: Partial subtitles are never submission content; final text uses MCP only with a valid semantic receipt.
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
    this.phase = "starting";
    document.getElementById("answer-countdown").textContent = window.AppI18n?.t("voice_auto_starting") ?? "Opening microphone…";
    let previousText = "";
    const capture = new SpeechCapture(
      /** Refresh activity only for changed current text and show safe subtitles. */ (text) => { if (epoch === this.epoch && this.capture === capture) { if (text !== previousText) this.activity(); previousText = text; this.subtitle(text); } },
      /**
 *  Inputs: Full final provider text, finalization latency and optional receipt. Outputs: None. Logic: Stop clock, resolve explicit-end waiter or submit once; absent receipt selects the normal automatic answer path. Constraints: No partial-text substitution or invented response.
 */ (text, finalizationMs, receipt) => {
        if (epoch !== this.epoch || this.capture !== capture) return;
        this.capture = null;
        this.clearAnswerTimer();
        this.phase = "finalizing";
        this.finalTranscript = text.trim();
        this.completionReceipt = receipt ?? null;
        // Every recording closes automatically. A revoked semantic receipt uses the explicit
        // inactivity/capture-limit answer route; final words still pass normal input/output review.
        this.finishRequested = true;
        console.info("Interview final speech received", { endRequested: this.finishRequested, textLength: this.finalTranscript?.length ?? 0 });
        this.subtitle(this.finalTranscript ?? "");
        if (!receipt) this.autoFinish = false;
        if (!this.finalTranscript) this.message(window.AppI18n?.t("voice_empty") ?? "No speech recognized; recording as unanswered.");
        this.finalWaiter?.resolve(this.finalTranscript);
        this.finalWaiter = null;
        document.getElementById("speech-metrics").textContent += ` · ${(window.AppI18n?.t("voice_recording_metrics", { recording: Math.round(performance.now() - started), finalization: finalizationMs ?? "—" }) ?? `Recording with transcript ${Math.round(performance.now() - started)} ms · STT finalization ${finalizationMs ?? "—"} ms`)}`;
        this.updateControls();
        this.submitTranscript();
      },
      /** Release recording controls after a current recognition failure. */ (text) => {
        if (epoch !== this.epoch || this.capture !== capture) return;
        console.error("Interview speech recognition failed", { phase: "capture_or_finalize", endRequested: this.finishRequested });
        this.capture = null; this.finishRequested = false; this.finalTranscript = null;
        this.clearAnswerTimer();
        this.phase = "error";
        document.getElementById("answer-countdown").hidden = true;
        this.finalWaiter?.reject(new Error(text));
        this.finalWaiter = null;
        this.autoFinish = false; this.completionReceipt = null;
        this.message(text); this.updateControls();
      },
      { questionId: this.completionEnabled ? this.question.question_id : null,
        /** Only voiced PCM belonging to this capture refreshes the silence deadline. */ onActivity: () => { if (epoch === this.epoch && this.capture === capture) this.activity(); },
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
      this.countdown("answering", 5);
      this.message(this.completionEnabled
        ? (window.AppI18n?.t("voice_auto_recording") ?? "Recording. Five seconds of silence or detected completion submits automatically.")
        : (window.AppI18n?.t("voice_recording") ?? "Recording. Five seconds of silence submits automatically."));
      this.setState("listening");
      this.updateControls();
    } catch (error) {
      if (epoch === this.epoch && this.capture === capture) {
        console.error("Interview microphone startup failed", { name: error.name });
        this.capture = null; this.finishRequested = false;
        this.clearAnswerTimer();
        this.phase = "error";
        document.getElementById("answer-countdown").hidden = true;
        this.message(error.message); this.updateControls();
      }
    }
  }

  /**
 *  Input: clearSubtitle (default true); Output: none; release capture/playback and invalidate confirmation and late callbacks.
 * agent.js submits with false to retain final subtitles; other new question/cancel/page leave paths clear subtitles; does not alter business state.
 */ reset(clearSubtitle = true) {
    this.clearAnswerTimer();
    this.phase = "idle";
    this.suspended = false;
    this.finalWaiter?.reject(new Error("Interview ended before transcription completed."));
    this.finalWaiter = null;
    document.getElementById("answer-countdown").hidden = true;
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
