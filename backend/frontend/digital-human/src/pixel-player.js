/**
 * @module pixel-player
 * UE 5.8 官方播放器包装；语音识别模块独立管理麦克风。
 *
 * 目录：
 * - AvatarPlayer：管理单个数字人串流连接及控制消息。
 * - AvatarPlayer.constructor：保存容器、事件回调和连接状态。
 * - AvatarPlayer.connect：验证本机地址并建立播放连接，关闭设备和游戏输入上行。
 * - AvatarPlayer.connect.callback1：解析 UE 回应并确认面试控制器已经就绪。
 * - AvatarPlayer.connect.callback2：连接建立后启动有次数上限的控制器探测。
 * - AvatarPlayer.connect.callback2.callback1：发送 ping 并在探测超限时提示绑定检查。
 * - AvatarPlayer.connect.callback3：断开时清理播放器并通知语音降级。
 * - AvatarPlayer.connect.callback4：连接失败时清理播放器并通知语音降级。
 * - AvatarPlayer.connect.callback5：自动播放被拒绝时提示用户点击播放。
 * - AvatarPlayer.connect.callback6：将有效视频帧率转交给页面统计。
 * - AvatarPlayer.failed：撤销就绪状态、释放连接并上报断开事件。
 * - AvatarPlayer.send：仅在控制器就绪后发送交互消息。
 * - AvatarPlayer.close：停止探测计时器并释放播放器。
 *
 * 关键变量：
 * （无模块级变量。）
 *
 * 关键状态说明：
 * ready 仅由 avatar_ready 确认；pingTimer 属于当前连接。
 * UseMic 与 MouseInput 均关闭；页面麦克风和 UE 音频播放分别管理。
 */
import { Config, PixelStreaming } from "@epicgames-ps/lib-pixelstreamingfrontend-ue5.8";

/** Minimal official UE 5.8 player. The separate STT capture owns the microphone. */
export class AvatarPlayer {
  /** 保存容器、事件回调和连接状态。 */
  constructor(container, onEvent, onStatus) {
    this.container = container;
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.player = null;
    this.ready = false;
    this.pingTimer = null;
  }

  /** 验证本机地址并建立播放连接，关闭设备和游戏输入上行。 */
  connect(url) {
    const endpoint = new URL(url);
    if (endpoint.protocol !== "ws:" || !["127.0.0.1", "localhost"].includes(endpoint.hostname)) {
      throw new Error("Use a local ws:// signalling server.");
    }
    if (this.player) {
      this.player.play();
      const video = this.container.querySelector("video");
      if (video) video.muted = false;
      return;
    }
    const config = new Config({ initialSettings: {
      ss: url, AutoConnect: false, AutoPlayVideo: true, StartVideoMuted: false,
      UseMic: false, UseCamera: false, KeyboardInput: false, MouseInput: false,
      TouchInput: false, GamepadInput: false, WaitForStreamer: true,
    }});
    const player = new PixelStreaming(config, { videoElementParent: this.container });
    this.player = player;
    /** 解析 UE 回应并确认面试控制器已经就绪。 */
    player.addResponseEventListener("interviewer", (payload) => {
      try {
        const event = JSON.parse(payload);
        if (event.type === "avatar_ready") {
          this.ready = !event.detail;
          clearInterval(this.pingTimer);
          this.onStatus(this.ready ? "数字人已连接" : event.detail);
        }
        this.onEvent(event);
      } catch { this.onStatus("数字人返回了无效消息"); }
    });
    /** 连接建立后启动有次数上限的控制器探测。 */
    player.addEventListener("webRtcConnected", () => {
      let attempts = 0;
      this.pingTimer = setInterval(/** 发送 ping 并在探测超限时提示绑定检查。 */ () => {
        player.emitUIInteraction({ type: "ping" });
        if (++attempts >= 10) {
          clearInterval(this.pingTimer);
          if (!this.ready) this.onStatus("画面已连接，但面试控制器未响应；请检查 L_Interview 中的控制器。");
        }
      }, 500);
      this.onStatus("画面已连接，正在检查面试控制器…");
    });
    /** 断开时清理播放器并通知语音降级。 */
    player.addEventListener("webRtcDisconnected", () => this.failed("数字人已断开，使用语音／文字模式"));
    /** 连接失败时清理播放器并通知语音降级。 */
    player.addEventListener("webRtcFailed", () => this.failed("数字人连接失败，使用语音／文字模式"));
    /** 自动播放被拒绝时提示用户点击播放。 */
    player.addEventListener("playStreamRejected", () => this.onStatus("点击连接／播放按钮启用声音"));
    /** 将有效视频帧率转交给页面统计。 */
    player.addEventListener("statsReceived", ({ data }) => {
      const fps = data.aggregatedStats.inboundVideoStats.framesPerSecond;
      if (typeof fps === "number" && Number.isFinite(fps)) this.onEvent({ type: "avatar_stats", fps });
    });
    player.connect();
  }

  /** 撤销就绪状态、释放连接并上报断开事件。 */
  failed(message) {
    this.ready = false;
    clearInterval(this.pingTimer);
    this.onStatus(message);
    this.onEvent({ type: "avatar_disconnected" });
    const player = this.player;
    this.player = null;
    player?.disconnect();
  }

  /** 仅在控制器就绪后发送交互消息。 */
  send(message) {
    return this.ready && this.player?.emitUIInteraction(message);
  }

  /** 停止探测计时器并释放播放器。 */
  close() {
    this.ready = false;
    clearInterval(this.pingTimer);
    const player = this.player;
    this.player = null;
    player?.disconnect();
  }
}
