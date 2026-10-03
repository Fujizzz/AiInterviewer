/**
 * @module interview-camera
 * Responsibilities: Local camera preview and multilingual device prompts on interview page; does not request microphone, upload, or record media.
 * Implementation: Explicit click to request video stream; release tracks on close, device termination, or pagehide; isolate late authorization results by sequence number; prompts resolved by i18n.js.
 * Related Modules: agent.html provides video, placeholder, and button; i18n.js provides language service; independent of agent.js business logic and question budget.
 * Declaration Index:
 * - cameraText: read camera status text and perform named parameter interpolation.
 * - cameraText.callback1: convert error types and other parameters into display text.
 * - releaseCamera: invalidate pending request, stop active tracks, and reset preview.
 * - onCameraEnded: release remaining resources and report status upon unexpected device termination.
 * - toggleCamera: enable, cancel request, or disable camera based on state; fail explicitly without retry.
 * - onCameraPageHide: release local media and invalidate pending permission results when leaving page.
 * Variable Index:
 * - cameraVideo: local muted video element.
 * - cameraPlaceholder: placeholder area shown when not playing.
 * - cameraButton: explicit start/stop button.
 * - cameraStatus: accessible status and error messages.
 * - cameraStream: current stream held by module, empty value indicates no active stream.
 * - cameraPending: whether currently waiting for authorization or video.play completion.
 * - cameraGeneration: request sequence number, incremented on close or leave to reject late results.
 * - CAMERA_FALLBACK: English status text used when standalone client tests omit i18n.js.
 * - cameraText: generates camera status prompt in current language.
 * Constraints:
 * Browser device authorization may arrive after close request returns; late streams must be stopped immediately and cannot reactivate preview.
 */
const cameraVideo = document.getElementById("self-video");
const cameraPlaceholder = document.getElementById("camera-placeholder");
const cameraButton = document.getElementById("camera-toggle");
const cameraStatus = document.getElementById("camera-status");
let cameraStream = null;
let cameraPending = false;
let cameraGeneration = 0;
const CAMERA_FALLBACK = {
  "camera_enable": "Enable camera",
  "camera_closed": "Camera closed; local preview only, nothing recorded or uploaded.",
  "camera_note": "Camera is for self-preview only; nothing is recorded or uploaded.",
  "camera_unavailable": "Camera is unavailable here. Use a supported browser over localhost or HTTPS.",
  "camera_waiting": "Waiting for camera permission. Choose Allow in the browser, or cancel.",
  "camera_on": "Camera enabled · local preview only, nothing recorded or uploaded.",
  "camera_interrupted": "Camera connection ended. Check the device and enable it again.",
  "camera_permission": "Camera permission was denied. Allow it in site settings and try again.",
  "camera_not_found": "No camera was found. Connect one and try again.",
  "camera_not_readable": "The camera could not be read. Check whether another app is using it.",
  "camera_failed": "Could not enable camera ({error}). Check the browser and device."
};
/**
 *  Return camera status text; use English fallback in standalone test context without i18n module.
 */
const cameraText = (key, values = {}) => {
  const template = window.AppI18n?.t(key, values) ?? CAMERA_FALLBACK[key] ?? key;
  return template.replace(/\{(\w+)\}/g, /**
 *  Convert error parameters into display text.
 */ (_, name) => String(values[name] ?? `{${name}}`));
};

/**
 * Function: Release preview and invalidate uncompleted requests. Input: message string and module media state. Output: none; stops all held tracks and resets DOM. Logic: increment sequence number first, then release, to prevent late Promise from overriding new state.
 * Constraints: Does not revoke browser permissions, does not affect interview connection; safe to call repeatedly, sends no network data.
 */
function releaseCamera(message) {
  cameraGeneration += 1;
  cameraPending = false;
  if (cameraStream) {
    for (const track of cameraStream.getTracks()) {
      track.removeEventListener("ended", onCameraEnded);
      track.stop();
    }
  }
  cameraStream = null;
  cameraVideo.srcObject = null;
  cameraVideo.hidden = true;
  cameraPlaceholder.hidden = false;
  cameraButton.textContent = cameraText("camera_enable");
  cameraButton.setAttribute("aria-pressed", "false");
  cameraStatus.textContent = message;
}

/**
 * Function: Report device termination. Input: triggered implicitly by track ended event, reads current media state. Output: none.
 * Logic: unified cleanup, displays actionable prompt, logs non-sensitive warnings; does not auto-reapply permissions.
 */
function onCameraEnded() {
  console.warn("[interview-camera] Video track ended; local preview stopped.");
  releaseCamera(cameraText("camera_interrupted"));
}

/**
 * Function: Explicitly start or stop local preview. Input: triggered implicitly by button event, reads module state and browser media capabilities. Output: Promise<void>; successfully binds video stream, fails capture permission, device, or playback exceptions and displays diagnostics.
 * Logic: capture sequence number on each request, verify authorization and playback separately; late streams after cancellation are only released.
 * Constraints: requests only video, does not record or upload; logs contain only phase and exception name, no device identifiers or media content.
 */
async function toggleCamera() {
  if (cameraStream || cameraPending) {
    releaseCamera(cameraText("camera_closed"));
    console.info("[interview-camera] Preview closed by user.");
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    cameraStatus.textContent = cameraText("camera_unavailable");
    console.warn("[interview-camera] getUserMedia is unavailable.");
    return;
  }
  const generation = ++cameraGeneration;
  cameraPending = true;
  cameraButton.textContent = window.AppI18n?.language() === "zh" ? "取消开启" : "Cancel";
  cameraStatus.textContent = cameraText("camera_waiting");
  console.info("[interview-camera] Requesting local video permission.");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    if (generation !== cameraGeneration) {
      for (const track of stream.getTracks()) track.stop();
      return;
    }
    cameraStream = stream;
    for (const track of stream.getVideoTracks()) track.addEventListener("ended", onCameraEnded);
    cameraVideo.srcObject = stream;
    cameraVideo.hidden = false;
    await cameraVideo.play();
    if (generation !== cameraGeneration) return;
    cameraPending = false;
    cameraPlaceholder.hidden = true;
    cameraButton.textContent = window.AppI18n?.language() === "zh" ? "关闭摄像头" : "Disable camera";
    cameraButton.setAttribute("aria-pressed", "true");
    cameraStatus.textContent = cameraText("camera_on");
    console.info("[interview-camera] Local preview is playing.");
  } catch (error) {
    if (generation !== cameraGeneration) return;
    const messages = {
      NotAllowedError: cameraText("camera_permission"),
      NotFoundError: cameraText("camera_not_found"),
      NotReadableError: cameraText("camera_not_readable"),
    };
    console.error("[interview-camera] Preview failed", { name: error.name });
    releaseCamera(messages[error.name] || cameraText("camera_failed", { error: error.name || (window.AppI18n?.language() === "zh" ? "未知错误" : "unknown error") }));
  }
}

/**
 * Function: Clean up media when leaving. Input: Implicit pagehide event. Output: None; do not retain video, does not affect existing interview cleaner.
 */
function onCameraPageHide() {
  releaseCamera(cameraText("camera_closed"));
}

cameraButton.addEventListener("click", toggleCamera);
window.addEventListener("pagehide", onCameraPageHide);
