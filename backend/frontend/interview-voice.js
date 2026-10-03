/**
 * @module interview-voice
 * 职责：协调语音播放、实时字幕和用户点击结束后的最终转写提交；不计算面试评价。
 * 实现：epoch 和采集对象身份隔离迟到事件；最终转写与结束确认均满足才回调 agent.js 一次。
 * 关联：SpeechCapture 管理 PCM/STT；agent.js 提供提交回调和当前题可回答状态。
 *
 * 目录：
 * - InterviewVoice：
 *   Coordinate subtitles and explicitly confirmed speech answers, isolating stale playback/STT events.
 * - InterviewVoice.constructor：
 *   Store the answer callback and initialize speech, subtitle and end-confirmation state.
 * - InterviewVoice.constructor.callback1：
 *   Connect the avatar when the user clicks its playback button.
 * - InterviewVoice.constructor.callback2：
 *   Replay the current question on explicit request.
 * - InterviewVoice.constructor.callback3：
 *   Interrupt the active question and restore answer controls.
 * - InterviewVoice.constructor.callback4：
 *   Start one microphone recognition session.
 * - InterviewVoice.constructor.callback5：
 *   Confirm answer completion and flush captured PCM before submission.
 * - InterviewVoice.constructor.callback6：
 *   Stop question playback when automatic speech is disabled.
 * - InterviewVoice.constructor.callback7：
 *   Apply the main interview's answer eligibility to voice controls.
 * - InterviewVoice.message：
 *   Show a plain-text presentation status without rendering model output as HTML.
 * - InterviewVoice.updateControls：
 *   Gate capture/end buttons during playback, finalization and pending interview requests.
 * - InterviewVoice.subtitle：
 *   Render plain-text subtitles in the video stage and follow the newest lines.
 * - InterviewVoice.finishAnswer：
 *   Register explicit end confirmation; flush active capture or submit already-finalized speech.
 * - InterviewVoice.submitTranscript：
 *   Consume confirmed final text once and call the supplied interview submission boundary.
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
 *   Capture one voice answer, showing partial and final subtitles without an editable input.
 * - InterviewVoice.record.callback1：
 *   Display draft words only while this capture epoch remains current.
 * - InterviewVoice.record.callback2：
 *   Retain final text; submit only after explicit end confirmation, never on the capture limit alone.
 * - InterviewVoice.record.callback3：
 *   Release recording controls after a current recognition failure.
 * - InterviewVoice.reset：
 *   Cancel resources/confirmation; optionally preserve submitted subtitles while awaiting the next question.
 * - InterviewVoice.close：
 *   Release capture, playback and streaming resources when leaving the page.
 *
 * 关键变量：
 * （无模块级变量。）
 */
import { SpeechCapture } from "./speech-capture.js";

/** 功能：语音与字幕协调；逻辑：按 epoch、对象身份和用户结束确认提交；约束：不评价或隐式重试。
 * 实例状态：onAnswer 为 agent.js 提交边界；finalTranscript 为待确认最终文本，finishRequested 为
 * 本轮显式结束确认；capture/epoch 防止旧采集结果误提交；其余状态管理播放和耗时。 */
export class InterviewVoice {
  /** 输入 onAnswer(text) 回调，输出新协调器；仅最终非空文本且用户显式结束时调用。
   * 初始无题目/采集/确认；注册按钮和可回答状态监听，不请求设备或网络。 */ constructor(onAnswer) {
    this.onAnswer = onAnswer;
    this.finalTranscript = null;
    this.finishRequested = false;
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

  /** 无参数；读取可回答/播放/采集/结束状态更新按钮，无网络或提交副作用。
   * 收尾中禁止重复结束；录音时限触发的最终文本仍需按钮确认，不自动提交。 */ updateControls() {
    const recording = !!this.capture;
    document.getElementById("start-recording").disabled = !this.eligible || this.busy || recording || this.finalTranscript !== null;
    document.getElementById("stop-recording").disabled = !this.eligible || this.busy || this.finishRequested || (!this.capture?.recording && this.finalTranscript === null);
    document.getElementById("replay-question").disabled = !this.eligible || recording || this.finalTranscript !== null;
    document.getElementById("interrupt-speech").disabled = !this.busy;
    document.getElementById("voice-enabled").disabled = recording || this.finalTranscript !== null;
  }

  /** 输入当前转写文本；输出无。textContent 防止转写作为 HTML 执行，空文本隐藏字幕。
   * 长回答在视频内独立滚动，跟随最新文本；字幕不保存到浏览器持久存储。 */ subtitle(text) {
    const node = document.getElementById("answer-subtitle");
    node.textContent = text;
    document.getElementById("answer-subtitles").hidden = !text;
    node.scrollTop = node.scrollHeight;
  }

  /** 用户点击结束回答：仅当前题空闲时登记确认；正在录音则等待 PCM flush 和最终 STT。
   * 已因原 120 秒时限收尾的文本可直接确认；重复点击、失败和空白不会产生回答请求。 */ async finishAnswer() {
    if (!this.eligible || this.busy || this.finishRequested) return;
    if (this.finalTranscript !== null) { this.finishRequested = true; this.submitTranscript(); return; }
    if (!this.capture?.recording) return;
    this.finishRequested = true;
    console.info("Interview speech answer end confirmed", { epoch: this.epoch });
    this.message(window.AppI18n?.t("voice_finalizing") ?? "正在完成转写并提交回答…");
    this.updateControls();
    await this.capture.end();
  }

  /** 无外部参数；消费已确认的非空最终转写并回调 onAnswer(text)，仅执行一次。
   * 清除待提交状态后调用 agent.js；当前题和请求 UUID 再由业务模块校验，不提交部分字幕。 */ submitTranscript() {
    if (!this.finishRequested || !this.finalTranscript || !this.eligible || this.busy || this.capture) return;
    const text = this.finalTranscript;
    this.finalTranscript = null;
    this.finishRequested = false;
    this.updateControls();
    console.info("Interview speech answer ready for submission", { textLength: text.length });
    this.onAnswer(text);
  }

  /** Load the official player bundle and connect the local signalling endpoint. */ async connectAvatar() {
    try {
      if (!this.player) {
        const { AvatarPlayer } = await import("/stream-demo/pixel-player.js");
        this.player = new AvatarPlayer(document.getElementById("avatar-view"),
          /** Route UE playback events through utterance identity checks. */ (event) => this.avatarEvent(event), /** Display connection status supplied by the avatar player. */ (text) => this.message(text));
      }
      this.player.connect(document.getElementById("signalling-url").value.trim());
    } catch (error) { this.message(`数字人不可用：${error.message}；可继续语音面试。`); }
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
        this.message(`问题朗读不可用：${error.message}；可阅读题目并开始语音回答。`);
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
    /** Release controls and report a current audio download failure. */ this.audio.onerror = () => { if (epoch === this.epoch) { this.stopPlayback(); this.message("问题音频加载失败，可阅读题目并开始语音回答。"); } };
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
    if (this.question) this.message(window.AppI18n?.t("voice_listening") ?? "朗读已停止，点击“开始回答”进行语音作答。");
    this.updateControls();
  }

  /** 无参数；当前题可回答且播放结束时启动一轮采集；实时字幕不作为提交内容。
   * 最终文本按 epoch/采集身份校验，只有显式结束确认才能提交；失败日志只记阶段和状态。
   * 采集默认时限/编码/供应商超时保持原条件，不引入文字输入或自动重录。 */ async record() {
    if (!this.eligible || this.busy || this.capture || this.finalTranscript !== null) return;
    this.stopPlayback();
    const epoch = this.epoch;
    const started = performance.now();
    this.finishRequested = false;
    this.subtitle("");
    const capture = new SpeechCapture(
      /** Display draft words only while this capture epoch remains current. */ (text) => { if (epoch === this.epoch && this.capture === capture) this.subtitle(text); },
      /** Retain final text; submit only after explicit end confirmation, never on the capture limit alone. */ (text, finalizationMs) => {
        if (epoch !== this.epoch || this.capture !== capture) return;
        this.capture = null;
        this.finalTranscript = text.trim() || null;
        console.info("Interview final speech received", { endRequested: this.finishRequested, textLength: this.finalTranscript?.length ?? 0 });
        this.subtitle(this.finalTranscript ?? "");
        if (!this.finalTranscript) {
          this.finishRequested = false;
          this.message(window.AppI18n?.t("voice_empty") ?? "未识别到语音，请点击“开始回答”重新录音。");
        } else if (!this.finishRequested) {
          this.message(window.AppI18n?.t("voice_final_ready") ?? "录音已结束，点击“结束回答”提交字幕中的回答。");
        }
        document.getElementById("speech-metrics").textContent += ` · 录音含转录 ${Math.round(performance.now() - started)} ms · STT 收尾 ${finalizationMs ?? "—"} ms`;
        this.updateControls();
        this.submitTranscript();
      },
      /** Release recording controls after a current recognition failure. */ (text) => {
        if (epoch !== this.epoch || this.capture !== capture) return;
        console.error("Interview speech recognition failed", { phase: "capture_or_finalize", endRequested: this.finishRequested });
        this.capture = null; this.finishRequested = false; this.finalTranscript = null;
        this.message(text); this.updateControls();
      },
    );
    this.capture = capture;
    this.message(window.AppI18n?.t("voice_starting") ?? "正在开启麦克风与英文识别…");
    this.updateControls();
    try {
      await capture.start();
      if (epoch !== this.epoch || capture.closed || this.capture !== capture) { await capture.close(); return; }
      console.info("Interview speech capture started", { epoch });
      this.message(window.AppI18n?.t("voice_recording") ?? "正在录音，回答结束后点击“结束回答”提交。");
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

  /** 输入 clearSubtitle（默认 true）；释放采集/播放并作废确认与迟到回调，输出无。
   * agent.js 提交时传 false 保留最终字幕，其余新题/取消/离页路径清空字幕；不改变业务状态。 */ reset(clearSubtitle = true) {
    this.stopPlayback();
    void this.capture?.close();
    this.capture = null;
    this.question = null;
    this.finalTranscript = null;
    this.finishRequested = false;
    if (clearSubtitle) this.subtitle("");
    this.updateControls();
  }

  /** Release capture, playback and streaming resources when leaving the page. */ close() { this.reset(); this.setState("idle"); this.player?.close(); }
}
