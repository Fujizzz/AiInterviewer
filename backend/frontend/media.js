/**
 * @module media
 * Responsibilities: Generate synthetic or explicitly selected device media for transport diagnostics, capture bounded chunks, and return only verified media.
 * Implementation: Use MediaRecorder to serialize chunks through StreamClient, enforce queue and duration bounds, and localize user-facing failures through AppI18n.
 * Related Modules: app.js selects test modes, stream-client.js validates transport payloads, and view.js presents playback and status.
 *
 * Declaration Index:
 * - createSyntheticSource:
 *   Create canvas and synthetic tone source, return stream and cleanup callback.
 * - createSyntheticSource.draw:
 *   Draw one frame of observable test image; frame number used only for visual identification, not as recording clock.
 * - createSyntheticSource.cleanup:
 *   Release all resources created by this source; idempotent flag prevents duplicate closing from pagehide and finally.
 * - createSyntheticSource.cleanup.callback1:
 *   Stop one media track of synthetic source, release its capture resources.
 * - createDeviceSource:
 *   Request explicitly specified microphone or camera, do not switch input source.
 * - createDeviceSource.cleanup:
 *   Stop all device tracks; do not create recording file or export to disk.
 * - createDeviceSource.cleanup.callback1:
 *   Stop one media track of device source, end device capture.
 * - captureAndEcho:
 *   Process tail chunks serially, return replayable Blob after verification.
 * - captureAndEcho.stop:
 *   Stop generating new chunks, retain dataavailable before onstop for validation queue termination.
 * - captureAndEcho.callback1:
 *   Log recording end and error callback, use Promise to await recorder stop.
 * - captureAndEcho.callback1.recorder.onerror:
 *   Save recording error and request stop, unblock wait to enter error handling.
 * - captureAndEcho.recorder.ondataavailable:
 *   Record bytes to be processed and submit Blob serially; stop recording on error, maintain surface success without dropping frames.
 * - captureAndEcho.recorder.ondataavailable.callback1:
 *   Send current Blob serially, decrement pending byte count on completion or failure.
 * - captureAndEcho.recorder.ondataavailable.callback2:
 *   Record chunk processing failure and stop recording, pass error to outer flow.
 *
 * Variable Index:
 * None
 *
 * Key State Explanation:
 * Source object owns stream/cleanup; recorder manages encoding, queuedBytes tracks unprocessed chunks, processing serializes verification; error text provided by i18n.js.
 * recordingError stores first capture or upload error; echoed collects verified payloads. Retain 250 ms chunk target, 3/30 seconds recording duration limit, and 4 MiB waiting upper bound.
 */

/**
 * Create MediaStream composed of canvas video and synthetic tone.
 * Method: 10 FPS canvas connected to 440 Hz tone, routed to in-memory audio destination, not output to speaker.
 * Returns: {stream, cleanup}; cleanup stops timer, track, and AudioContext, idempotent and repeatable.
 */
export async function createSyntheticSource(canvas) {
  if (!canvas.captureStream || !window.AudioContext) {
    throw new Error(window.AppI18n?.t("media_canvas_unsupported") ?? "This browser does not support canvas capture or Web Audio.");
  }
  const context = canvas.getContext("2d");
  let frame = 0;
  /**
 *  Render a frame for observable test visualization; frame number is used only for visual identification, not as recording clock.
 */
  function draw() {
    context.fillStyle = `hsl(${frame * 3 % 360}, 45%, 25%)`;
    context.fillRect(0, 0, 640, 360);
    context.fillStyle = "white";
    context.font = "28px sans-serif";
    context.fillText("WebSocket media echo", 40, 150);
    context.fillText(`Frame ${frame++}`, 40, 210);
  }
  draw();
  const timer = setInterval(draw, 100);
  const stream = canvas.captureStream(10);
  const audio = new AudioContext();
  const oscillator = audio.createOscillator();
  const destination = audio.createMediaStreamDestination();
  oscillator.frequency.value = 440;
  oscillator.connect(destination);
  stream.addTrack(destination.stream.getAudioTracks()[0]);
  let cleaned = false;
  /**
 *  Release all resources created by this source; idempotent flag prevents duplicate audio closure during pagehide and finally.
 */
  async function cleanup() {
    if (cleaned) return;
    cleaned = true;
    clearInterval(timer);
    /**
 *  Stop one media track from the source and release its capture resources.
 */
    stream.getTracks().forEach((track) => track.stop());
    await audio.close();
  }
  try {
    await audio.resume();
    oscillator.start();
    return { stream, cleanup };
  } catch (error) {
    await cleanup();
    throw error;
  }
}

/**
 *
 * Request explicitly specified real devices, without automatic fallback or replacement of input sources.
 * Parameters: mode is either audio or video; video mode captures audio simultaneously.
 * Returns: {stream, cleanup}; browser permission or device exceptions are passed directly to upper layer for display.
 *
 */
export async function createDeviceSource(mode) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(window.AppI18n?.t("media_device_unsupported") ?? "This browser cannot capture media. Open the page over localhost or HTTPS.");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: mode === "video" });
  /**
 *  Stop all device tracks; no recording file is created or exported to disk.
 */
  async function cleanup() {
    /**
 *  Stop one media track from the device source and end device capture.
 */
    stream.getTracks().forEach((track) => track.stop());
  }
  return { stream, cleanup };
}

/**
 *
 * Complete one round of capture, chunk upload, and media reassembly.
 * Inputs: connected StreamClient, mode, DemoView, and stop/cleanup callback registrars.
 * Method: Promise chain serially processes dataavailable; waits for final Blob completion after onstop.
 * Returns: complete returned Blob. Encoding, device, backpressure, or verification errors are thrown; partial success is not reported.
 *
 */
export async function captureAndEcho(connection, mode, view, registerControls) {
  const mimeType = mode === "audio" ? "audio/webm;codecs=opus" : "video/webm;codecs=vp8,opus";
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported(mimeType)) {
    throw new Error(window.AppI18n?.t("media_format_unsupported", { mime: mimeType }) ?? `This browser does not support ${mimeType}. Use a browser that supports this format.`);
  }
  const source = mode === "synthetic"
    ? await createSyntheticSource(view.element("source"))
    : await createDeviceSource(mode);
  let recorder = null;
  let autoStop;
  /**
 *  Stop generating new chunks, retain the last dataavailable before onstop for queue validation termination.
 */
  function stop() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
    view.element("stop").disabled = true;
  }
  registerControls(stop, source.cleanup);
  try {
    view.preview(source.stream);
    await connection.start(mode, mimeType);
    recorder = new MediaRecorder(source.stream, { mimeType });
    const echoed = [];
    let queuedBytes = 0;
    let processing = Promise.resolve();
    let recordingError = null;
    const stopped = new Promise(/**
 *  Log recording end and error callbacks, and wait via Promise for the recorder to stop.
 */ (resolve) => {
      recorder.onstop = resolve;
      /**
 *  Save recording error and request stop, while releasing the end-waiting state to enter error handling.
 */
      recorder.onerror = (event) => {
        recordingError = event.error || new Error(window.AppI18n?.t("media_recording_failed") ?? "MediaRecorder failed.");
        stop();
        resolve();
      };
    });
    /**
 *  Record pending bytes and submit Blobs serially; on error, stop capture without dropping frames to maintain surface success.
 */
    recorder.ondataavailable = ({ data }) => {
      if (!data.size || recordingError) return;
      queuedBytes += data.size;
      if (queuedBytes > 4 * 1024 * 1024) {
        recordingError = new Error(window.AppI18n?.t("media_backpressure") ?? "More than 4 MiB of media is waiting to be sent; the test stopped.");
        stop();
        return;
      }
      processing = processing.then(/**
 *  Serially send current Blob and decrement pending byte count upon completion or failure.
 */ async () => {
        if (recordingError) return;
        try {
          echoed.push(await connection.sendChunk(data));
        } finally {
          queuedBytes -= data.size;
        }
      }).catch(/**
 *  Record chunk processing failure and stop recording, passing the error to outer flow for propagation.
 */ (error) => { recordingError = error; stop(); });
    };
    recorder.start(250);
    view.element("stop").disabled = false;
    view.status(window.AppI18n?.t("media_capturing") ?? "Capturing · echoing chunks");
    view.log(window.AppI18n?.t("media_record_start", { mime: mimeType }) ?? `MediaRecorder started · ${mimeType} · 250 ms target chunk interval`);
    autoStop = setTimeout(stop, mode === "synthetic" ? 3000 : 30000);
    await stopped;
    await processing;
    if (recordingError) throw recordingError;
    if (!echoed.length) throw new Error(window.AppI18n?.t("media_empty") ?? "No media chunks were produced for verification.");
    const blob = new Blob(echoed, { type: recorder.mimeType });
    view.log(window.AppI18n?.t("media_echo_rebuilt", { bytes: blob.size }) ?? `Echoed media reassembled · ${blob.size} bytes`);
    return blob;
  } finally {
    clearTimeout(autoStop);
    stop();
    await source.cleanup();
    registerControls(null, null);
    view.preview(null);
  }
}
