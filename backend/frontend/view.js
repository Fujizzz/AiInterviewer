/**
 * @module view
 * 功能：封装诊断页 DOM、回放资源管理和当前语言结果文案，不执行网络请求或设备采集。
 *
 * 目录：
 * - DemoView：
 *   管理诊断页面的 DOM 展示、忙碌状态与回放 URL 生命周期。
 * - DemoView.constructor：
 *   初始化页面状态及可释放资源；仅保存文档引用，不访问持久存储。
 * - DemoView.element：
 *   按 ID 取得本页唯一元素，集中 DOM 查找入口以避免散落的全局依赖。
 * - DemoView.log：
 *   在页面追加带时间的诊断信息，裁剪旧行；不写 console 或文件。
 * - DemoView.status：
 *   设置可访问状态文字及错误样式；error 只控制展示，不改变业务状态。
 * - DemoView.busy：
 *   在测试期间禁用重复启动和清空操作；停止采集按钮由媒体控制器独立启用。
 * - DemoView.busy.callback1：
 *   按测试忙碌状态启用或禁用一个测试按钮，避免重复启动。
 * - DemoView.progress：
 *   显示已校验分片的累计计数与 RTT；不将已发送数据误计为已验证数据。
 * - DemoView.reset：
 *   重置单次测试指标并释放上一段回放，保留有限的页面历史日志。
 * - DemoView.preview：
 *   将 MediaStream 交给本地预览；传入 null 可解除元素对采集流的引用。
 * - DemoView.releasePlayback：
 *   停止解码、清除 src 并撤销 Blob URL，保证播放器不继续保留旧媒体。
 * - DemoView.playback：
 *   接收已通过回传校验的完整媒体 Blob，为其创建单个临时回放 URL。
 * - DemoView.success：
 *   展示服务端完成确认；ping 与媒体校验使用不同文字以避免零分片误导。
 * - DemoView.failure：
 *   展示失败并撤销可能尚未获最终确认的回放；不触发重连或重试。
 * - DemoView.clear：
 *   清空当前指标、页面日志和合成画布，主动释放可见测试结果。
 *
 * 关键变量：
 * （无模块级变量。）
 *
 * 关键状态说明：
 * document 为调用者提供的 DOM；playbackUrl 至多指向当前回放，替换或离开页面时释放。日志只在页面内保留，不写 console、文件或浏览器持久存储；结果文案由 AppI18n 按当前语言生成。
 */
/** 管理诊断页面的 DOM 展示、忙碌状态与回放 URL 生命周期。 */
export class DemoView {
  /** 初始化页面状态及可释放资源；仅保存文档引用，不访问持久存储。 */
  constructor(document) {
    this.document = document;
    this.playbackUrl = null;
  }

  /** 按 ID 取得本页唯一元素，集中 DOM 查找入口以避免散落的全局依赖。 */
  element(id) {
    return this.document.getElementById(id);
  }

  /** 在页面追加带时间的诊断信息，裁剪旧行；不写 console 或文件。 */
  log(message) {
    const lines = this.element("log").textContent.split("\n").filter(Boolean);
    lines.push(`${new Date().toLocaleTimeString()}  ${message}`);
    this.element("log").textContent = lines.slice(-30).join("\n");
  }

  /** 设置可访问状态文字及错误样式；error 只控制展示，不改变业务状态。 */
  status(text, error = false) {
    this.element("status").textContent = text;
    this.element("status").classList.toggle("error", error);
  }

  /** 在测试期间禁用重复启动和清空操作；停止采集按钮由媒体控制器独立启用。 */
  busy(value) {
    /** 按测试忙碌状态启用或禁用一个测试按钮，避免重复启动。 */
    this.document.querySelectorAll("[data-test]").forEach((button) => {
      button.disabled = value;
    });
    this.element("clear").disabled = value;
    this.element("stop").disabled = true;
  }

  /** 显示已校验分片的累计计数与 RTT；不将已发送数据误计为已验证数据。 */
  progress({ chunks, bytes, rttMs }) {
    this.element("chunks").textContent = String(chunks);
    this.element("bytes").textContent = String(bytes);
    this.element("latency").textContent = rttMs.toFixed(1);
  }

  /** 重置单次测试指标并释放上一段回放，保留有限的页面历史日志。 */
  reset() {
    this.releasePlayback();
    this.element("chunks").textContent = "0";
    this.element("bytes").textContent = "0";
    this.element("latency").textContent = "—";
    this.element("summary").textContent = window.AppI18n?.t("stream_running") ?? "测试进行中…";
  }

  /** 将 MediaStream 交给本地预览；传入 null 可解除元素对采集流的引用。 */
  preview(stream) {
    this.element("preview").srcObject = stream;
  }

  /** 停止解码、清除 src 并撤销 Blob URL，保证播放器不继续保留旧媒体。 */
  releasePlayback() {
    const video = this.element("playback");
    video.pause();
    video.removeAttribute("src");
    video.load();
    if (this.playbackUrl) URL.revokeObjectURL(this.playbackUrl);
    this.playbackUrl = null;
  }

  /** 接收已通过回传校验的完整媒体 Blob，为其创建单个临时回放 URL。 */
  playback(blob) {
    this.releasePlayback();
    this.playbackUrl = URL.createObjectURL(blob);
    this.element("playback").src = this.playbackUrl;
  }

  /** 展示服务端完成确认；ping 与媒体校验使用不同文字以避免零分片误导。 */
  success(mode, summary) {
    const t = (key, values = {}) => window.AppI18n?.t(key, values) ?? key;
    this.status(t("stream_pass"));
    this.element("summary").textContent = mode === "ping"
      ? t("stream_pass_ping")
      : t("stream_pass_media", { chunks: summary.chunk_count, bytes: summary.byte_count });
    this.log(t("stream_complete_log", { chunks: summary.chunk_count, bytes: summary.byte_count }));
  }

  /** 展示失败并撤销可能尚未获最终确认的回放；不触发重连或重试。 */
  failure(error) {
    const t = (key, values = {}) => window.AppI18n?.t(key, values) ?? key;
    this.status(t("stream_failed"), true);
    this.element("summary").textContent = t("stream_fail_summary", { message: error.message });
    this.log(t("stream_fail_log", { message: error.message }));
    this.releasePlayback();
  }

  /** 清空当前指标、页面日志和合成画布，主动释放可见测试结果。 */
  clear() {
    this.reset();
    this.element("source").getContext("2d").clearRect(0, 0, 640, 360);
    this.element("log").textContent = "";
    const t = (key) => window.AppI18n?.t(key) ?? key;
    this.element("summary").textContent = t("stream_cleared");
    this.status(t("status_ready"));
  }
}
