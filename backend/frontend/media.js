/**
 * @module media
 * 功能：音视频测试来源与采集流程：生成或获取 MediaStream，经 MediaRecorder 分片并由 StreamClient 校验回传。
 *
 * 目录：
 * - createSyntheticSource：
 *   创建画布与合成音调来源，返回流及清理回调。
 * - createSyntheticSource.draw：
 *   绘制一帧可观察测试画面；帧号仅用于视觉识别，不作为录制时钟。
 * - createSyntheticSource.cleanup：
 *   释放该来源创建的全部资源；幂等标志避免 pagehide 与 finally 重复关闭音频。
 * - createSyntheticSource.cleanup.callback1：
 *   停止合成来源的一条媒体轨道，释放其采集资源。
 * - createDeviceSource：
 *   请求明确指定的麦克风或摄像头，不更换输入来源。
 * - createDeviceSource.cleanup：
 *   停止所有设备轨道；不创建录制文件或向磁盘导出。
 * - createDeviceSource.cleanup.callback1：
 *   停止设备来源的一条媒体轨道，结束设备采集。
 * - captureAndEcho：
 *   串行处理末尾分片，完成校验后返回可回放 Blob。
 * - captureAndEcho.stop：
 *   停止产生新分片，保留 onstop 前的末尾 dataavailable，供验证队列收尾。
 * - captureAndEcho.callback1：
 *   登记录制结束与错误回调，用 Promise 等待录制器停止。
 * - captureAndEcho.callback1.recorder.onerror：
 *   保存录制错误并请求停止，同时解除结束等待以进入错误处理。
 * - captureAndEcho.recorder.ondataavailable：
 *   记录待处理字节并串行提交 Blob；出错时停止采集，不丢帧维持表面成功。
 * - captureAndEcho.recorder.ondataavailable.callback1：
 *   串行发送当前 Blob，并在完成或失败时扣减等待字节数。
 * - captureAndEcho.recorder.ondataavailable.callback2：
 *   记录分片处理失败并停止录制，将错误交由外层流程传播。
 *
 * 关键变量：
 * （无模块级变量。）
 *
 * 关键状态说明：
 * 来源对象拥有 stream/cleanup；recorder 管理编码，queuedBytes 统计未处理分片，processing 串行化校验。
 * recordingError 保存首次采集或回传错误；echoed 收集已验证载荷。保留 250 ms 分片目标、3/30 秒录制期限及 4 MiB 等待上限。
 */

/**
 * 创建画布视频与合成音调组成的 MediaStream。
 * 方法：10 FPS 画布与 440 Hz 音调连接到内存音频目的地，不输出到扬声器。
 * 返回：{stream, cleanup}；cleanup 停止定时器、轨道及 AudioContext，可重复调用。
 */
export async function createSyntheticSource(canvas) {
  if (!canvas.captureStream || !window.AudioContext) {
    throw new Error("浏览器不支持画布采集或 Web Audio。");
  }
  const context = canvas.getContext("2d");
  let frame = 0;
  /** 绘制一帧可观察测试画面；帧号仅用于视觉识别，不作为录制时钟。 */
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
  /** 释放该来源创建的全部资源；幂等标志避免 pagehide 与 finally 重复关闭音频。 */
  async function cleanup() {
    if (cleaned) return;
    cleaned = true;
    clearInterval(timer);
    /** 停止合成来源的一条媒体轨道，释放其采集资源。 */
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
 * 请求明确指定的真实设备，不自动降级或替换输入来源。
 * 参数：mode 为 audio 或 video；video 模式同时采集音频。
 * 返回：{stream, cleanup}；浏览器权限或设备异常原样交由上层显示。
 */
export async function createDeviceSource(mode) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("浏览器无法采集媒体，请通过 localhost 或 HTTPS 打开页面。");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: mode === "video" });
  /** 停止所有设备轨道；不创建录制文件或向磁盘导出。 */
  async function cleanup() {
    /** 停止设备来源的一条媒体轨道，结束设备采集。 */
    stream.getTracks().forEach((track) => track.stop());
  }
  return { stream, cleanup };
}

/**
 * 完成一次采集、分片回传和媒体重组。
 * 输入：已连接的 StreamClient、mode、DemoView，以及 stop/cleanup 回调注册器。
 * 方法：Promise 链串行处理 dataavailable；onstop 后等待末尾 Blob 完成验证。
 * 返回：完整回传 Blob。编码、设备、背压或校验错误均抛出，不报告部分成功。
 */
export async function captureAndEcho(connection, mode, view, registerControls) {
  const mimeType = mode === "audio" ? "audio/webm;codecs=opus" : "video/webm;codecs=vp8,opus";
  if (!window.MediaRecorder || !MediaRecorder.isTypeSupported(mimeType)) {
    throw new Error(`浏览器不支持 ${mimeType}，请使用支持该格式的浏览器。`);
  }
  const source = mode === "synthetic"
    ? await createSyntheticSource(view.element("source"))
    : await createDeviceSource(mode);
  let recorder = null;
  let autoStop;
  /** 停止产生新分片，保留 onstop 前的末尾 dataavailable，供验证队列收尾。 */
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
    const stopped = new Promise(/** 登记录制结束与错误回调，用 Promise 等待录制器停止。 */ (resolve) => {
      recorder.onstop = resolve;
      /** 保存录制错误并请求停止，同时解除结束等待以进入错误处理。 */
      recorder.onerror = (event) => {
        recordingError = event.error || new Error("MediaRecorder failed.");
        stop();
        resolve();
      };
    });
    /** 记录待处理字节并串行提交 Blob；出错时停止采集，不丢帧维持表面成功。 */
    recorder.ondataavailable = ({ data }) => {
      if (!data.size || recordingError) return;
      queuedBytes += data.size;
      if (queuedBytes > 4 * 1024 * 1024) {
        recordingError = new Error("待发送媒体超过 4 MiB，网络未及时回传，测试已停止。");
        stop();
        return;
      }
      processing = processing.then(/** 串行发送当前 Blob，并在完成或失败时扣减等待字节数。 */ async () => {
        if (recordingError) return;
        try {
          echoed.push(await connection.sendChunk(data));
        } finally {
          queuedBytes -= data.size;
        }
      }).catch(/** 记录分片处理失败并停止录制，将错误交由外层流程传播。 */ (error) => { recordingError = error; stop(); });
    };
    recorder.start(250);
    view.element("stop").disabled = false;
    view.status("采集中 · 分片实时回传");
    view.log(`MediaRecorder 开始 · ${mimeType} · 分片目标间隔 250 ms`);
    autoStop = setTimeout(stop, mode === "synthetic" ? 3000 : 30000);
    await stopped;
    await processing;
    if (recordingError) throw recordingError;
    if (!echoed.length) throw new Error("没有产生可校验的媒体分片。");
    const blob = new Blob(echoed, { type: recorder.mimeType });
    view.log(`回传媒体已重组 · ${blob.size} bytes`);
    return blob;
  } finally {
    clearTimeout(autoStop);
    stop();
    await source.cleanup();
    registerControls(null, null);
    view.preview(null);
  }
}
