/**
 * @module speech-stream-tests
 * Responsibilities: Verify incremental browser speech, protocol failures and question cancellation.
 * Implementation: Use controlled byte streams and a deterministic Web Audio double; no provider, device or UE calls.
 * Related Modules: speech-playback parses/schedules audio; interview-voice owns question state and CSRF.
 * Declaration Index:
 * - audio: Build a fixed PCM event; samples are little-endian and split below the wire limit.
 * - end: Declare exact successful byte accounting, without treating it as audible completion.
 * - Wire: Supply a controllable response whose producer can remain open after the first audio.
 * - Wire.constructor: Save the stream controller; body cancellation is observable without real HTTP.
 * - Wire.constructor.object1.start: Capture the consumer stream for incremental delivery.
 * - Wire.constructor.object1.cancel: Record cancellation after parser completion or failure.
 * - Wire.send: Append complete records; an omitted close keeps synthesis visibly unfinished.
 * - Wire.send.callback1: Serialize fixture records with exact NDJSON framing.
 * - Wire.close: Finish transport independently of application completion.
 * - AudioClock: Model Web Audio timing and source cleanup without claiming acoustic-device validation.
 * - AudioClock.constructor: Record sources and begin in the suspended browser state.
 * - AudioClock.resume: Permit autoplay in tests that are not explicitly testing refusal/cancellation.
 * - AudioClock.close: Close only this utterance's context.
 * - AudioClock.createBuffer: Preserve normalized samples for byte-order and amplitude assertions.
 * - AudioClock.createBuffer.object1.copyToChannel: Copy samples just as a real AudioBuffer would.
 * - AudioClock.createBufferSource: Return a source with an independently triggered ended event.
 * - AudioClock.createBufferSource.source.connect: Accept the local output destination.
 * - AudioClock.createBufferSource.source.disconnect: Mark disconnection for leak assertions.
 * - AudioClock.createBufferSource.source.start: Capture the exact scheduled timestamp.
 * - AudioClock.createBufferSource.source.stop: Mark cancellation without synthesizing an ended callback.
 * - page: Create minimal controls and window events for the real InterviewVoice coordinator.
 * - page.globalThis.document.getElementById: Allocate each control once; token and status use the production lookup path.
 * - page.globalThis.window.addEventListener: No external listeners are needed for these local lifecycle tests.
 * - flush: Drain reader and fetch continuations without real timer or network delays.
 * - player: Build a player with directly inspectable lifecycle events and automatic test cleanup.
 * - player.callback1: Keep scheduled callbacks under explicit test control.
 * - player.callback2: No real timer was allocated.
 * - player.object1.onStarted: Record onset independently of terminal input.
 * - player.object1.onEnded: Record audible drain.
 * - player.object1.onError: Preserve the safe failure for assertions.
 * - player.callback3: Stop all mocked resources even after an assertion fails.
 * - callback1: First chunks must schedule before EOF; exact sample boundaries avoid click-producing gaps.
 * - callback1.object1.onStart: Format is validated by the real parser.
 * - callback1.object1.onEnd: Observe verified input completion, then release short-input buffering.
 * - callback2: Short utterances start after verified end; signed PCM must retain sample values and order.
 * - callback3: Underrun is explicit and terminal, with no rescheduling or replay of already heard speech.
 * - callback4: Invalid EOF, order, sizes and terminal records must never invoke successful completion.
 * - callback4.object2.onStart: Parsing alone is under test.
 * - callback4.object2.onAudio: Partial audio is permissible before a terminal failure.
 * - callback4.object2.onEnd: No malformed stream may grant completion.
 * - callback5: A browser waiting for an autoplay gesture must still obey the request cancellation deadline.
 * - callback5.callback1: Keep resume unresolved until cancellation.
 * - callback5.callback1.callback1: Intentionally emulate a browser waiting for a gesture.
 * - callback6: Real coordinator must send CSRF, remain reading until drain, and arm preparation exactly afterwards.
 * - callback6.callback1: Capture the authenticated request without network I/O.
 * - callback6.callback2: Clear coordinator timers and sources after each isolated question.
 * - callback7: Midstream failure or a new question must stop queued audio, fence late callbacks and avoid another TTS call.
 * - callback7.callback1: Supply exactly one controllable utterance.
 * Variable Index:
 * - ID: Fixed utterance identity shared by the offline protocol fixtures.
 * - START: Required PCM format declaration.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { readSpeechStream, PcmStreamPlayer } from "../../speech-playback.js";
import { InterviewVoice } from "../../interview-voice.js";

const ID = "11111111-1111-4111-8111-111111111111";
const START = { type: "start", utterance_id: ID, sample_rate: 24000, channels: 1, sample_width: 2 };

/** Build a fixed PCM event; samples are little-endian and split below the wire limit. */
function audio(sequence = 0, length = 8192) {
  return { type: "audio", sequence, pcm: Buffer.alloc(length).toString("base64") };
}
/** Declare exact successful byte accounting, without treating it as audible completion. */
function end(bytes) { return { type: "end", utterance_id: ID, audio_bytes: bytes, generation_ms: 500 }; }

/** Supply a controllable response whose producer can remain open after the first audio. */
class Wire {
  /** Save the stream controller; body cancellation is observable without real HTTP. */
  constructor() {
    this.cancelled = false;
    this.response = new Response(new ReadableStream({
      /** Capture the consumer stream for incremental delivery. */
      start: (controller) => { this.controller = controller; },
      /** Record cancellation after parser completion or failure. */
      cancel: () => { this.cancelled = true; },
    }), { headers: { "Content-Type": "application/x-ndjson" } });
  }
  /** Append complete records; an omitted close keeps synthesis visibly unfinished. */
  send(...events) {
    this.controller.enqueue(new TextEncoder().encode(events.map(
      /** Serialize fixture records with exact NDJSON framing. */
      (event) => JSON.stringify(event) + "\n").join("")));
  }
  /** Finish transport independently of application completion. */
  close() { this.controller.close(); }
}

/** Model Web Audio timing and source cleanup without claiming acoustic-device validation. */
class AudioClock {
  /** Record sources and begin in the suspended browser state. */
  constructor() { this.currentTime = 10; this.state = "suspended"; this.destination = {}; this.sources = []; }
  /** Permit autoplay in tests that are not explicitly testing refusal/cancellation. */
  async resume() { this.state = "running"; }
  /** Close only this utterance's context. */
  async close() { this.state = "closed"; }
  /** Preserve normalized samples for byte-order and amplitude assertions. */
  createBuffer(channels, count, rate) {
    assert.equal(channels, 1); assert.equal(rate, 24000);
    return { count, /** Copy samples just as a real AudioBuffer would. */
      copyToChannel(samples) { this.samples = samples.slice(); } };
  }
  /** Return a source with an independently triggered ended event. */
  createBufferSource() {
    const source = {
      /** Accept the local output destination. */ connect() {},
      /** Mark disconnection for leak assertions. */ disconnect() { this.disconnected = true; },
      /** Capture the exact scheduled timestamp. */ start(at) { this.at = at; },
      /** Mark cancellation without synthesizing an ended callback. */ stop() { this.stopped = true; },
    };
    this.sources.push(source);
    return source;
  }
}

/** Create minimal controls and window events for the real InterviewVoice coordinator. */
function page() {
  const elements = new Map();
  globalThis.document = {
    /** Allocate each control once; token and status use the production lookup path. */
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, { checked: false, textContent: "", content: "offline-csrf" });
      return elements.get(id);
    },
  };
  globalThis.window = { /** No external listeners are needed for these local lifecycle tests. */ addEventListener() {} };
}

/** Drain reader and fetch continuations without real timer or network delays. */
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); }

/** Build a player with directly inspectable lifecycle events and automatic test cleanup. */
async function player(t) {
  t.mock.method(globalThis, "setTimeout", /** Keep scheduled callbacks under explicit test control. */ () => 1);
  t.mock.method(globalThis, "clearTimeout", /** No real timer was allocated. */ () => {});
  globalThis.AudioContext = AudioClock;
  const events = [];
  const playback = new PcmStreamPlayer({
    /** Record onset independently of terminal input. */ onStarted: () => events.push("started"),
    /** Record audible drain. */ onEnded: () => events.push("ended"),
    /** Preserve the safe failure for assertions. */ onError: (error) => events.push(error.message),
  });
  t.after(/** Stop all mocked resources even after an assertion fails. */ () => playback.stop());
  await playback.prepare();
  return { playback, events };
}

/** First chunks must schedule before EOF; exact sample boundaries avoid click-producing gaps. */
test("stream schedules contiguous PCM before synthesis completes and waits for audible drain", async (t) => {
  const { playback, events } = await player(t);
  const wire = new Wire();
  let inputEnded = false;
  const reading = readSpeechStream(wire.response, {
    /** Format is validated by the real parser. */ onStart() {},
    onAudio: playback.append.bind(playback),
    /** Observe verified input completion, then release short-input buffering. */
    onEnd() { inputEnded = true; playback.finish(); },
  });
  wire.send(START, audio(0), audio(1));
  await flush();
  assert.equal(inputEnded, false);
  assert.equal(playback.context.sources.length, 2);
  const [first, second] = playback.context.sources;
  assert.equal(first.at, 10.06);
  assert.ok(Math.abs(second.at - first.at - 4096 / 24000) < 1e-12);
  wire.send(end(16384)); wire.close(); await reading;
  assert.equal(inputEnded, true); assert.deepEqual(events, []);
  first.onended(); assert.deepEqual(events, []);
  second.onended(); assert.deepEqual(events, ["ended"]);
  assert.equal(playback.context.state, "closed");
});

/** Short utterances start after verified end; signed PCM must retain sample values and order. */
test("short stream preserves signed little-endian samples and stops every queued source", async (t) => {
  const { playback, events } = await player(t);
  playback.append(new Uint8Array([0, 128, 255, 127, 0, 0]));
  assert.equal(playback.context.sources.length, 0);
  playback.finish();
  const source = playback.context.sources[0];
  assert.deepEqual([...source.buffer.samples], [-1, 32767 / 32768, 0]);
  playback.stop(); playback.stop();
  assert.equal(source.stopped, true); assert.equal(source.onended, null);
  assert.deepEqual(events, []);
});

/** Underrun is explicit and terminal, with no rescheduling or replay of already heard speech. */
test("underrun stops playback once without inventing completion", async (t) => {
  const { playback, events } = await player(t);
  playback.append(new Uint8Array(8192)); playback.append(new Uint8Array(8192));
  const [first, second] = playback.context.sources;
  first.onended(); second.onended();
  assert.equal(events.length, 1); assert.match(events[0], /stopped arriving/);
  playback.append(new Uint8Array(8192)); playback.finish();
  assert.equal(playback.context.sources.length, 2); assert.equal(events.length, 1);
});

/** Invalid EOF, order, sizes and terminal records must never invoke successful completion. */
test("parser rejects incomplete, malformed and failed streams", async () => {
  const cases = [
    [START, audio()],
    [START, audio(1)],
    [START, audio(0, 3)],
    [START, audio(), end(2)],
    [START, audio(), { type: "error", error: { detail: "Provider failed" } }],
    [START, audio(), end(8192), audio(1)],
  ];
  for (const events of cases) {
    const wire = new Wire(); wire.send(...events); wire.close();
    await assert.rejects(readSpeechStream(wire.response, {
      /** Parsing alone is under test. */ onStart() {},
      /** Partial audio is permissible before a terminal failure. */ onAudio() {},
      /** No malformed stream may grant completion. */ onEnd() { assert.fail("Invalid stream completed"); },
    }));
  }
});

/** A browser waiting for an autoplay gesture must still obey the request cancellation deadline. */
test("pending autoplay authorization can be cancelled before any provider request", async (t) => {
  const { playback } = await player(t);
  playback.stop();
  t.mock.method(AudioClock.prototype, "resume", /** Keep resume unresolved until cancellation. */ () => new Promise(
    /** Intentionally emulate a browser waiting for a gesture. */ () => {}));
  const next = new PcmStreamPlayer({});
  const abort = new AbortController();
  const ready = next.prepare(abort.signal);
  abort.abort();
  await assert.rejects(ready, { name: "AbortError" });
  next.stop(); assert.equal(next.context.state, "closed");
});

/** Real coordinator must send CSRF, remain reading until drain, and arm preparation exactly afterwards. */
test("browser coordinator uses streaming with CSRF and waits for audio drain", async (t) => {
  await player(t); page();
  const wire = new Wire(); let request;
  t.mock.method(globalThis, "fetch", /** Capture the authenticated request without network I/O. */ async (url, options) => {
    request = { url, options }; return wire.response;
  });
  const voice = new InterviewVoice();
  voice.eligible = true; voice.question = { text: "Describe your contribution." };
  t.after(/** Clear coordinator timers and sources after each isolated question. */ () => voice.close());
  const speaking = voice.speak(); await flush();
  assert.equal(request.url, "/api/speech/tts/stream/");
  assert.equal(request.options.headers["X-CSRFToken"], "offline-csrf");
  wire.send(START, audio(), audio(1)); await flush();
  const playback = voice.audioStream;
  assert.equal(playback.context.sources.length, 2);
  assert.equal(voice.deadline, null);
  wire.send(end(16384)); wire.close(); await speaking;
  assert.equal(voice.busy, true); assert.equal(voice.deadline, null);
  for (const source of playback.context.sources) source.onended();
  assert.equal(voice.busy, false);
  assert.ok(voice.deadline > performance.now());
  assert.equal(request.options.signal.aborted, true);
});

/** Midstream failure or a new question must stop queued audio, fence late callbacks and avoid another TTS call. */
test("provider failure and question replacement stop partial audio without replay", async (t) => {
  await player(t); page();
  for (const replace of [false, true]) {
    const wire = new Wire(); let calls = 0;
    t.mock.method(globalThis, "fetch", /** Supply exactly one controllable utterance. */ async () => { calls++; return wire.response; });
    const voice = new InterviewVoice();
    voice.eligible = true; voice.question = { text: "Old question." };
    try {
      const speaking = voice.speak(); await flush();
      wire.send(START, audio(), audio(1)); await flush();
      const playback = voice.audioStream;
      if (replace) voice.setQuestion({ text: "New question." });
      wire.send({ type: "error", error: { detail: "Provider failed" } });
      wire.close(); await speaking;
      assert.equal(playback.stopped, true); assert.equal(voice.busy, false); assert.equal(calls, 1);
      for (const source of playback.context.sources) assert.equal(source.stopped, true);
      if (replace) assert.equal(voice.question.text, "New question.");
    } finally { voice.close(); }
  }
});
