/**
 * @module interview-camera
 * 职责：面试页本地摄像头预览和多语言设备提示；不申请麦克风、不上传、不录制媒体。
 * 实现：显式点击后申请视频流；关闭、设备终止或 pagehide 时释放轨道，序号隔离迟到的授权结果；提示由 i18n.js 解析。
 * 关联：agent.html 提供 video、占位及按钮，i18n.js 提供语言服务；与 agent.js 的业务连接和题目预算独立。
 * 目录：
 * - releaseCamera：使待决申请失效，停止已有轨道并重置预览。
 * - onCameraEnded：设备意外终止时释放其余资源并报告状态。
 * - toggleCamera：根据状态开启、取消申请或关闭摄像头，失败明确显示且不重试。
 * - onCameraPageHide：离开页面时释放本地媒体并使待决权限结果失效。
 * 关键变量：
 * - cameraVideo：本地静音视频元素。
 * - cameraPlaceholder：未播放时显示的占位区域。
 * - cameraButton：显式启停按钮。
 * - cameraStatus：可访问的状态和错误提示。
 * - cameraStream：本模块当前持有的流，空值表示无活动流。
 * - cameraPending：当前是否等待授权或 video.play 完成。
 * - cameraGeneration：申请序号，关闭或离开时递增以拒绝迟到结果。
 * - CAMERA_FALLBACK / cameraText：为独立客户端测试提供中文回退并生成当前语言提示。
 * 约束：
 * 浏览器设备授权可能晚于关闭请求返回；迟到流必须立即停止，不能重新激活预览。
 */
const cameraVideo = document.getElementById("self-video");
const cameraPlaceholder = document.getElementById("camera-placeholder");
const cameraButton = document.getElementById("camera-toggle");
const cameraStatus = document.getElementById("camera-status");
let cameraStream = null;
let cameraPending = false;
let cameraGeneration = 0;
const CAMERA_FALLBACK = {
  camera_enable: "开启摄像头", camera_closed: "摄像头已关闭，仅本地预览，不录制、不上传。", camera_note: "摄像头仅供自我预览，不录制、不上传。",
  camera_unavailable: "当前环境无法使用摄像头，请使用支持摄像头的浏览器并通过 localhost 或 HTTPS 打开。", camera_waiting: "等待摄像头授权，请在浏览器中选择允许；也可取消开启。",
  camera_on: "摄像头已开启 · 仅本地预览，不录制、不上传。", camera_interrupted: "摄像头连接已中断，请检查设备后重新开启。", camera_permission: "摄像头权限被拒绝，请在浏览器站点设置中允许后重新开启。",
  camera_not_found: "未检测到摄像头，请连接设备后重新开启。", camera_not_readable: "无法读取摄像头，请检查设备是否被其他应用占用。", camera_failed: "摄像头开启失败（{error}），请检查浏览器和设备。",
};
/** 返回摄像头状态文案；独立测试上下文没有 i18n 模块时使用中文兼容回退。 */
const cameraText = (key, values = {}) => {
  const template = window.AppI18n?.t(key, values) ?? CAMERA_FALLBACK[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? `{${name}}`));
};

/**
 * 功能：释放预览并失效尚未完成的申请。输入：显示文字 message 及模块媒体状态。
 * 输出：无；停止所有持有轨道，重置 DOM。逻辑：先递增序号，再释放，避免迟到 Promise 覆盖新状态。
 * 约束：不撤销浏览器权限，不影响面试连接；可重复调用，不发送网络数据。
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
 * 功能：报告设备终止。输入：轨道 ended 事件隐式触发，读取当前媒体状态。输出：无。
 * 逻辑：统一清理，显示可操作提示并记录非敏感警告；不自动重新申请权限。
 */
function onCameraEnded() {
  console.warn("[interview-camera] Video track ended; local preview stopped.");
  releaseCamera(cameraText("camera_interrupted"));
}

/**
 * 功能：显式启停本地预览。输入：按钮事件隐式触发，读取模块状态及浏览器媒体能力。
 * 输出：Promise<void>；成功绑定视频流，失败捕获权限、设备及播放异常并显示诊断。
 * 逻辑：每次申请捕获序号，授权和播放后分别核验；取消后的迟到流仅释放。
 * 约束：仅申请 video，不录制或上传；日志仅包含阶段和异常名称，无设备标识或媒体内容。
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
  cameraButton.textContent = window.AppI18n?.language() === "en" ? "Cancel" : "取消开启";
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
    cameraButton.textContent = window.AppI18n?.language() === "en" ? "Disable camera" : "关闭摄像头";
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
    releaseCamera(messages[error.name] || cameraText("camera_failed", { error: error.name || (window.AppI18n?.language() === "en" ? "unknown error" : "未知错误") }));
  }
}

/** 功能：离开时清理媒体。输入：pagehide 隐式事件。输出：无；不保留视频，不影响既有面试清理器。 */
function onCameraPageHide() {
  releaseCamera(cameraText("camera_closed"));
}

cameraButton.addEventListener("click", toggleCamera);
window.addEventListener("pagehide", onCameraPageHide);
