/**
 * @module digital-human-check
 * Responsibilities: Provide independent avatar and speech diagnostics without interview or scoring fixtures.
 * Implementation: Preview complete facial behaviour locally or explicitly request one independent Agent plan for all four states. Load approved avatar settings before connecting; test TTS playback and generated-audio STT through the configured UE player and speech endpoint; release presentation/player resources on exit.
 * Related Modules: /stream-demo/pixel-player.js wraps the official UE player and exports the authenticated configuration loader; /stream-demo/pcm-resampler.js converts decoded samples for the STT WebSocket.
 *
 * Declaration Index:
 * - element: Read a diagnostic control by its unique ID.
 * - sendPresentation: Send bounded presentation messages through the ready avatar connection.
 * - presentationStatus: Display the plan's source/status without rendering model text as markup.
 * - previewExpression: Preview one semantic expression locally without a model call.
 * - planExpression: Request a plan for the diagnostic question without generating or submitting interview data.
 * - applyState: Apply a presentation state independently of the interview workflow.
 * - clearExpression: Clear the active plan and return smoothly to recorded facial animation.
 * - stopListeningPreview: Cancel simulated activity and fence the preview capture without touching speech or interview data.
 * - previewListening: Simulate bounded speech/pause activity for local nod and gaze preview without microphone or provider calls.
 * - previewListening.callback1: Deliver one scheduled activity observation for the current preview only.
 * - previewListening.callback2: End the current preview after its fixed duration.
 * - record: Append a bounded timestamped event log.
 * - controls: Apply connection, playback and cached-audio eligibility.
 * - onAvatar: Record actual UE playback events and ignore obsolete utterances.
 * - onConnection: Show the official player's current connection status.
 * - play: Send the cached WAV to UE without synthesizing it again.
 * - synthesize: Request actual TTS, keep the returned URL and begin playback.
 * - transcribe: Send generated test audio as PCM through the real STT WebSocket.
 * - transcribe.callback1: Encode one resampled sample as little-endian PCM16.
 * - transcribe.callback2: Keep the STT handshake promise's completion functions.
 * - transcribe.callback3: Keep the final-transcript promise's completion functions.
 * - transcribe.callback4: Consume a final-promise rejection if the handshake fails first.
 * - transcribe.callback5: Pace generated PCM at the real-time recognition rate.
 * - transcribe.socket.onmessage: Resolve the handshake or final transcript from the socket.
 * - transcribe.socket.onerror: Report a recognition connection failure.
 * - transcribe.socket.onclose: Reject an unexpected recognition disconnect.
 * - callback1: Load approved connection settings and connect the official avatar player.
 * - callback2: Run one explicit text-to-speech check.
 * - callback3: Stop the current UE utterance.
 * - callback4: Run one explicit transcription of cached test audio.
 * - callback5: Release the avatar connection when leaving the diagnostic page.
 *
 * Variable Index:
 * - element: Lookup function for the diagnostic page's unique control IDs.
 * - player: Official Pixel Streaming player and response channel.
 * - presentation: Independent, cancellable facial-plan coordinator using the existing avatar channel.
 * - audio: Most recently generated temporary WAV response.
 * - activeId: Utterance whose playback events may change the controls.
 * - busy: An outstanding TTS request or UE playback.
 * - listeningTimers: Scheduled local activity observations, cancelled on state changes or playback.
 * - listeningPreviewId: Identity that prevents cancelled preview callbacks from emitting observations.
 * - closed: Page-exit fence preventing a late configuration response from opening a player.
 *
 * Constraints:
 * Uses only generated or cached diagnostic audio, does not access the microphone, and does not submit interview answers or scoring data.
 */
import { AvatarPlayer, PresentationController, loadAvatarConfiguration } from "/stream-demo/pixel-player.js";
import { PCM16Resampler } from "/stream-demo/pcm-resampler.js";

/** Read a diagnostic control by its unique ID. */
const element = (id) => document.getElementById(id);
let audio = null;
let activeId = null;
let busy = false;
let listeningTimers = [];
let listeningPreviewId = null;
let closed = false;
const player = new AvatarPlayer(element("avatar"), onAvatar, onConnection);
const presentation = new PresentationController({ send: sendPresentation, onStatus: presentationStatus, bootstrapIdle: false });

/** Send only bounded presentation messages through the ready avatar connection. */
function sendPresentation(message) { return player.send(message); }

/** Display presentation source and status codes without rendering model text as markup. */
function presentationStatus(status) {
  element("presentation-status").textContent = `${status.source || status.type || "presentation"}${status.reason ? ` · ${status.reason}` : ""}`;
}

/** Preview a local semantic expression without calling a model or changing interview data. */
function previewExpression() {
  presentation.preview(element("expression").value, Number(element("expression-strength").value));
  if (activeId) presentation.bindUtterance(activeId);
}

/** Request a plan for the displayed diagnostic question; do not generate or submit a question. */
function planExpression() {
  void presentation.setQuestion({ question_id: `diagnostic-${crypto.randomUUID()}`, text: element("text").value });
  if (activeId) presentation.bindUtterance(activeId);
}

/** Apply a presentation state without changing the underlying question or answer workflow. */
function applyState() {
  stopListeningPreview();
  const state = element("presentation-state").value;
  presentation.setState(state);
  player.send({ type: "state", state });
}

/** Remove Agent ownership and restore the recorded fallback without a model request. */
function clearExpression() {
  stopListeningPreview();
  presentation.clear({ resumeIdle: false });
  presentationStatus({ source: "fallback", reason: "manual_release" });
}

/** Cancel simulated activity and fence its capture without changing interview or speech state. */
function stopListeningPreview() {
  for (const timer of listeningTimers) clearTimeout(timer);
  listeningTimers = [];
  if (listeningPreviewId) presentation.endListeningCapture();
  listeningPreviewId = null;
  element("listening-preview").textContent = "Preview listening rhythm";
}

/** Simulate short speech and pauses locally; generated nods remain occasional rather than forced acknowledgements. */
function previewListening() {
  if (listeningPreviewId) { stopListeningPreview(); return; }
  if (!player.ready || busy) return;
  if (!["model", "preview"].includes(presentation.plan?.source)) {
    element("status").textContent = "Select Preview local behaviour or apply an Agent plan before previewing attention gestures.";
    return;
  }
  element("presentation-state").value = "listening";
  presentation.setState("listening");
  player.send({ type: "state", state: "listening" });
  const captureId = presentation.beginListeningCapture();
  if (!captureId) return;
  listeningPreviewId = captureId;
  element("listening-preview").textContent = "Stop listening preview";
  presentation.observeListeningActivity(true);
  const observations = [[1000, true], [2000, true], [3000, true], [4000, true], [4500, false],
    [6500, true], [7500, true], [8500, true], [9500, true], [10000, false],
    [13000, true], [14000, true], [15000, true], [15500, false]];
  for (const [delay, active] of observations) {
    listeningTimers.push(setTimeout(/** Deliver an observation only while this simulated capture is current. */ () => {
      if (listeningPreviewId === captureId) presentation.observeListeningActivity(active);
    }, delay));
  }
  listeningTimers.push(setTimeout(/** Close the current simulated capture at its fixed deadline. */ () => {
    if (listeningPreviewId === captureId) stopListeningPreview();
  }, 17000));
  record("Local listening rhythm preview started; no microphone or model call.");
}

/** Append a bounded timestamped event log. */
function record(message) {
  const lines = element("events").textContent.split("\n").filter(Boolean);
  lines.push(`${new Date().toLocaleTimeString()} ${message}`);
  element("events").textContent = lines.slice(-30).join("\n");
}

/** Apply connection, playback and cached-audio eligibility. */
function controls() {
  for (const id of ["preview-expression", "clear-expression", "apply-state", "plan-expression"]) element(id).disabled = !player.ready;
  element("apply-state").disabled = !player.ready || busy;
  element("listening-preview").disabled = !player.ready || busy;
  element("speak").disabled = !player.ready || busy;
  element("replay").disabled = !player.ready || !audio || busy;
  element("stop").disabled = !activeId;
  element("transcribe").disabled = !audio || busy;
}

/** Record actual UE playback events and ignore obsolete utterances. */
function onAvatar(event) {
  presentation.onAvatarEvent(event);
  if (event.type === "avatar_stats") {
    element("fps").textContent = `${event.fps.toFixed(1)} FPS`;
    return;
  }
  record(`${event.type} ${event.utterance_id || ""} ${event.detail || ""}`.trim());
  if (event.type === "avatar_ready") controls();
  if (event.type === "avatar_disconnected" ||
      (event.utterance_id === activeId && ["playback_finished", "playback_failed", "interrupted"].includes(event.type))) {
    activeId = null;
    stopListeningPreview();
    busy = false;
    element("presentation-state").value = "listening";
    element("status").textContent = event.type;
    controls();
  }
  if (event.utterance_id === activeId && event.type === "playback_started") {
    presentation.setState("speaking");
    element("presentation-state").value = "speaking";
    element("status").textContent = "UE is speaking the test question.";
  }
}

/** Show the official player's current connection status. */
function onConnection(message) { element("connection").textContent = message; }

/** Send the cached WAV to UE without synthesizing it again. */
function play() {
  if (!audio || !player.ready) return;
  stopListeningPreview();
  activeId = audio.utterance_id;
  busy = true;
  element("status").textContent = "Waiting for UE playback…";
  presentation.bindUtterance(activeId);
  player.send({type:"speak", utterance_id:activeId, audio_url:audio.audio_url});
  controls();
}

/** Request actual TTS, keep the returned URL and begin playback. */
async function synthesize() {
  busy = true;
  controls();
  element("status").textContent = "Generating speech…";
  try {
    const headers = {"Content-Type":"application/json"};
    const csrf = element("csrf-token")?.content;
    if (csrf) headers["X-CSRFToken"] = csrf;
    const response = await fetch("/api/speech/tts/", {
      method:"POST", headers,
      body:JSON.stringify({text:element("text").value}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(`${result.error?.code}: ${result.error?.detail}`);
    audio = result;
    record(`TTS ${result.generation_ms} ms, ${result.sample_rate} Hz`);
    play();
  } catch (error) {
    busy = false;
    element("status").textContent = error.message;
    controls();
  }
}

/** Send generated test audio as PCM through the real STT WebSocket. */
async function transcribe() {
  element("transcribe").disabled = true;
  let context;
  let socket;
  try {
    const response = await fetch(audio.audio_url);
    if (!response.ok) throw new Error("Cached test audio has expired; generate it again.");
    context = new AudioContext();
    const decoded = await context.decodeAudioData(await response.arrayBuffer());
    const samples = new PCM16Resampler(decoded.sampleRate).process(decoded.getChannelData(0));
    const bytes = new Uint8Array(samples.length * 2);
    const view = new DataView(bytes.buffer);
    samples.forEach(/** Encode one resampled sample as little-endian PCM16. */ (sample, index) => view.setInt16(index * 2, sample, true));
    socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/speech/stt/`);
    let start;
    let finish;
    const started = new Promise(/** Keep the STT handshake promise's completion functions. */ (resolve, reject) => { start = {resolve, reject}; });
    const finished = new Promise(/** Keep the final-transcript promise's completion functions. */ (resolve, reject) => { finish = {resolve, reject}; });
    // A failure before handshake must not leave an unhandled final-promise rejection.
    void finished.catch(/** Consume a final-promise rejection if the handshake fails first. */ () => {});
    /** Resolve PCM handshake or final transcript from the socket. */
    socket.onmessage = ({data}) => {
      const event = JSON.parse(data);
      if (event.type === "hello") socket.send(JSON.stringify({type:"start"}));
      if (event.type === "started") start.resolve();
      if (event.type === "partial") element("transcript").textContent = event.text;
      if (event.type === "final") finish.resolve(event);
      if (event.type === "error") {
        const error = new Error(`${event.code}: ${event.detail}`);
        start.reject(error); finish.reject(error);
      }
    };
    /** Report a recognition connection failure. */
    socket.onerror = () => { const error = new Error("STT connection failed."); start.reject(error); finish.reject(error); };
    /** Reject an unexpected recognition disconnect. */
    socket.onclose = () => { const error = new Error("STT connection closed."); start.reject(error); finish.reject(error); };
    await started;
    element("status").textContent = "Sending generated test audio to ASR…";
    for (let offset = 0; offset < bytes.length; offset += 3200) {
      socket.send(bytes.slice(offset, offset + 3200));
      await new Promise(/** Pace generated PCM at the real-time recognition rate. */ (resolve) => setTimeout(resolve, 100));
    }
    socket.send(JSON.stringify({type:"stop"}));
    const result = await finished;
    element("transcript").textContent = result.text;
    element("status").textContent = "Test audio transcription completed.";
    record(`STT final ${result.finalization_ms} ms`);
  } catch (error) { element("status").textContent = error.message; }
  finally { socket?.close(); await context?.close(); controls(); }
}

/** Load approved settings and connect only after an explicit click; no model or microphone call. */
element("connect").onclick = async () => {
  if (closed) return;
  element("connect").disabled = true;
  try {
    const configuration = await loadAvatarConfiguration();
    if (closed) return;
    if (!configuration.enabled) throw new Error("The interviewer renderer is not configured.");
    element("signalling-url").textContent = configuration.signalling_url;
    player.connect(configuration.signalling_url);
  } catch (error) { onConnection(error.message); }
  finally { element("connect").disabled = false; }
};
/** Run one explicit text-to-speech check. */
element("speak").onclick = () => { void synthesize(); };
/** Replay cached audio without an additional TTS call. */
element("replay").onclick = play;
/** Stop the current UE utterance. */
element("stop").onclick = () => player.send({type:"stop"});
/** Run one explicit transcription of cached test audio. */
element("transcribe").onclick = () => { void transcribe(); };
element("preview-expression").onclick = previewExpression;
element("plan-expression").onclick = planExpression;
element("apply-state").onclick = applyState;
element("clear-expression").onclick = clearExpression;
element("listening-preview").onclick = previewListening;
/** Release the avatar connection when leaving the diagnostic page. */
window.addEventListener("pagehide", () => { closed = true; stopListeningPreview(); presentation.close(); player.close(); });
