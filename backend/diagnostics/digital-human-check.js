/**
 * @module digital-human-check
 * Independent local avatar and speech diagnostics; no interview or scoring fixtures.
 *
 * 目录：
 * - element：Read a diagnostic control by its unique ID.
 * - record：Append a bounded timestamped event log.
 * - controls：Apply connection, playback and cached-audio eligibility.
 * - onAvatar：Record actual UE playback events and ignore obsolete utterances.
 * - onConnection：Show the official player's current connection status.
 * - play：Send the cached WAV to UE without synthesizing it again.
 * - synthesize：Request actual TTS, keep the returned URL and begin playback.
 * - transcribe：Send generated test audio as PCM through the real STT WebSocket.
 * - transcribe.callback1：Encode one resampled sample as little-endian PCM16.
 * - transcribe.callback2：Keep the STT handshake promise's completion functions.
 * - transcribe.callback3：Keep the final-transcript promise's completion functions.
 * - transcribe.callback4：Consume a final-promise rejection if the handshake fails first.
 * - transcribe.callback5：Pace generated PCM at the real-time recognition rate.
 * - transcribe.socket.onmessage：Resolve the handshake or final transcript from the socket.
 * - transcribe.socket.onerror：Report a recognition connection failure.
 * - transcribe.socket.onclose：Reject an unexpected recognition disconnect.
 * - callback1：Connect the official local avatar player.
 * - callback2：Run one explicit text-to-speech check.
 * - callback3：Stop the current UE utterance.
 * - callback4：Run one explicit transcription of cached test audio.
 * - callback5：Release the avatar connection when leaving the diagnostic page.
 *
 * 关键变量：
 * - element：Lookup function for the diagnostic page's unique control IDs.
 * - player：Official Pixel Streaming player and response channel.
 * - audio：Most recently generated temporary WAV response.
 * - activeId：Utterance whose playback events may change the controls.
 * - busy：An outstanding TTS request or UE playback.
 */
import { AvatarPlayer } from "/stream-demo/pixel-player.js";
import { PCM16Resampler } from "/stream-demo/pcm-resampler.js";

/** Read a diagnostic control by its unique ID. */
const element = (id) => document.getElementById(id);
let audio = null;
let activeId = null;
let busy = false;
const player = new AvatarPlayer(element("avatar"), onAvatar, onConnection);

/** Append a bounded timestamped event log. */
function record(message) {
  const lines = element("events").textContent.split("\n").filter(Boolean);
  lines.push(`${new Date().toLocaleTimeString()} ${message}`);
  element("events").textContent = lines.slice(-30).join("\n");
}

/** Apply connection, playback and cached-audio eligibility. */
function controls() {
  element("speak").disabled = !player.ready || busy;
  element("replay").disabled = !player.ready || !audio || busy;
  element("stop").disabled = !activeId;
  element("transcribe").disabled = !audio || busy;
}

/** Record actual UE playback events and ignore obsolete utterances. */
function onAvatar(event) {
  if (event.type === "avatar_stats") {
    element("fps").textContent = `${event.fps.toFixed(1)} FPS`;
    return;
  }
  record(`${event.type} ${event.utterance_id || ""} ${event.detail || ""}`.trim());
  if (event.type === "avatar_ready") controls();
  if (event.type === "avatar_disconnected" ||
      (event.utterance_id === activeId && ["playback_finished", "playback_failed", "interrupted"].includes(event.type))) {
    activeId = null;
    busy = false;
    element("status").textContent = event.type;
    controls();
  }
  if (event.utterance_id === activeId && event.type === "playback_started") {
    element("status").textContent = "UE is speaking the test question.";
  }
}

/** Show the official player's current connection status. */
function onConnection(message) { element("connection").textContent = message; }

/** Send the cached WAV to UE without synthesizing it again. */
function play() {
  if (!audio || !player.ready) return;
  activeId = audio.utterance_id;
  busy = true;
  element("status").textContent = "Waiting for UE playback…";
  player.send({type:"speak", utterance_id:activeId, audio_url:audio.audio_url});
  controls();
}

/** Request actual TTS, keep the returned URL and begin playback. */
async function synthesize() {
  busy = true;
  controls();
  element("status").textContent = "Generating speech…";
  try {
    const response = await fetch("/api/speech/tts/", {
      method:"POST", headers:{"Content-Type":"application/json"},
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
    socket = new WebSocket(`ws://${location.host}/ws/speech/stt/`);
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

/** Connect the official local avatar player. */
element("connect").onclick = () => player.connect("ws://127.0.0.1:8889");
/** Run one explicit text-to-speech check. */
element("speak").onclick = () => { void synthesize(); };
/** Replay cached audio without an additional TTS call. */
element("replay").onclick = play;
/** Stop the current UE utterance. */
element("stop").onclick = () => player.send({type:"stop"});
/** Run one explicit transcription of cached test audio. */
element("transcribe").onclick = () => { void transcribe(); };
/** Release the avatar connection when leaving the diagnostic page. */
window.addEventListener("pagehide", () => player.close());
