/**
 * @module view
 * 功能：封装测试页的 DOM 更新及回放 Blob 生命周期，不执行网络或媒体采集。
 * 目录：DemoView；方法包括 element、log、status、busy、progress、reset、
 * preview、releasePlayback、playback、success、failure、clear。
 * 约束：日志仅保留最近 30 行；回放最多持有当前测试的一个 Blob URL。
 */
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
    this.element("summary").textContent = "测试进行中…";
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
    this.status("通过 · 回传校验完成");
    this.element("summary").textContent = mode === "ping"
      ? "PASS · 收到匹配的 pong 响应。结果仅在当前页面显示。"
      : `PASS · ${summary.chunk_count} 个分片 / ${summary.byte_count} bytes，全部通过 SHA-256 校验。结果仅在当前页面显示。`;
    this.log(`完成：${summary.chunk_count} 个分片，${summary.byte_count} bytes`);
  }

  /** 展示失败并撤销可能尚未获最终确认的回放；不触发重连或重试。 */
  failure(error) {
    this.status("测试失败", true);
    this.element("summary").textContent = `FAIL · ${error.message}`;
    this.log(`失败：${error.message}`);
    this.releasePlayback();
  }

  /** 清空当前指标、页面日志和合成画布，主动释放可见测试结果。 */
  clear() {
    this.reset();
    this.element("source").getContext("2d").clearRect(0, 0, 640, 360);
    this.element("log").textContent = "";
    this.element("summary").textContent = "已清空本次结果与媒体缓存。";
    this.status("就绪");
  }
}
