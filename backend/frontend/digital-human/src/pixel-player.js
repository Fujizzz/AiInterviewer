/**
 * @module pixel-player
 * Responsibilities: Wrap the official UE 5.8 pixel-streaming player; voice recognition manages the microphone separately.
 * Implementation: Validate signalling URLs against the page origin, suppress device/game input, probe controller readiness, and release the single active connection.
 * Related Modules: interview-voice.js owns speech capture and playback coordination; avatar-url-policy.js validates endpoints; avatar-configuration.js loads approved settings; i18n.js provides localized status messages.
 * Declaration Index:
 * - InterviewStream.release: Stop signalling, WebRTC and both media elements through the pinned SDK.
 * - AvatarPlayer: Manage one avatar stream connection and its control messages.
 * - AvatarPlayer.constructor: Store the container, event callbacks, and initial connection state.
 * - AvatarPlayer.text: Return localized status copy with an English fallback.
 * - AvatarPlayer.connect: Validate the endpoint against the page origin, configure the player, and register connection events.
 * - AvatarPlayer.connect.callback1: Parse UE responses and record controller readiness.
 * - AvatarPlayer.connect.callback2: Start a bounded readiness probe after WebRTC connects.
 * - AvatarPlayer.connect.callback2.callback1: Send a ping and report when controller probing reaches its limit.
 * - AvatarPlayer.connect.callback3: Clean up on disconnection and notify the voice interface.
 * - AvatarPlayer.connect.callback4: Clean up after connection failure and notify the voice interface.
 * - AvatarPlayer.connect.callback5: Prompt the user to enable playback after autoplay is rejected.
 * - AvatarPlayer.connect.callback6: Forward valid frame-rate statistics to the page.
 * - AvatarPlayer.failed: Clear readiness, release the connection, and report disconnection.
 * - AvatarPlayer.send: Send an interaction only after the controller is ready.
 * - AvatarPlayer.close: Stop the probe timer and release the player.
 *
 * Variable Index:
 * None
 *
 * Constraints:
 * ready is set only by avatar_ready; pingTimer belongs to the active connection. UseMic and MouseInput remain disabled.
 */
import { Config, PixelStreaming } from "@epicgames-ps/lib-pixelstreamingfrontend-ue5.8";
import { validateAvatarSignallingUrl } from "./avatar-url-policy.js";
export { PresentationController } from "./presentation-controller.js";
export { loadAvatarConfiguration } from "./avatar-configuration.js";

/** Extend the pinned Epic player to release its detached audio element as well as video. */
class InterviewStream extends PixelStreaming {
  /** Close signalling/WebRTC, then stop and destroy both media elements owned by this stream. */
  release() {
    this.disconnect();
    this._webRtcController.videoPlayer.getVideoElement().pause();
    this._webRtcController.streamController.audioElement.pause();
    this._webRtcController.destroyVideoPlayer();
  }
}

/** Minimal official UE 5.8 player. The separate STT capture owns the microphone. */
export class AvatarPlayer {
  /** Functionality: Initialize the player wrapper. Inputs: DOM container and event/status callbacks. Outputs: None; creates an unconnected wrapper. Constraints: Does not request devices or network access. */
  constructor(container, onEvent, onStatus) {
    this.container = container;
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    this.player = null;
    this.ready = false;
    this.pingTimer = null;
  }

  /** Connect to an approved local or same-origin signalling endpoint; device and game input remain disabled. */
  connect(url) {
    const endpoint = validateAvatarSignallingUrl(url, window.location.href);
    if (this.player) {
      this.player.play();
      const video = this.container.querySelector("video");
      if (video) video.muted = false;
      return;
    }
    const config = new Config({ initialSettings: {
      ss: endpoint, AutoConnect: false, AutoPlayVideo: true, StartVideoMuted: false,
      UseMic: false, UseCamera: false, KeyboardInput: false, MouseInput: false,
      TouchInput: false, GamepadInput: false, WaitForStreamer: true,
    }});
    const player = new InterviewStream(config, { videoElementParent: this.container });
    this.player = player;
    /** Parse UE responses and record whether the interviewer controller is ready. */
    player.addResponseEventListener("interviewer", (payload) => {
      if (this.player !== player) return;
      try {
        const event = JSON.parse(payload);
        if (event.type === "avatar_ready") {
          this.ready = !event.detail;
          clearInterval(this.pingTimer);
          this.onStatus(this.ready ? this.text("avatar_ready") : event.detail);
        }
        this.onEvent(event);
      } catch { this.onStatus(this.text("avatar_invalid_message")); }
    });
    /** Start a bounded controller-readiness probe after WebRTC connects. */
    player.addEventListener("webRtcConnected", () => {
      if (this.player !== player) return;
      clearInterval(this.pingTimer);
      let attempts = 0;
      this.pingTimer = setInterval(/** Send a ping and report when the bounded probe cannot confirm readiness. */ () => {
        if (this.player !== player) return;
        player.emitUIInteraction({ type: "ping" });
        if (++attempts >= 10) {
          clearInterval(this.pingTimer);
          if (!this.ready) this.onStatus(this.text("avatar_controller_unresponsive"));
        }
      }, 500);
      this.onStatus(this.text("avatar_checking_controller"));
    });
    /** Release player state on disconnect and notify the voice interface. */
    player.addEventListener("webRtcDisconnected", () => {
      if (this.player === player) this.failed(this.text("avatar_disconnected"));
    });
    /** Release player state on connection failure and notify the voice interface. */
    player.addEventListener("webRtcFailed", () => {
      if (this.player === player) this.failed(this.text("avatar_connection_failed"));
    });
    /** Prompt the user to enable playback after autoplay is rejected. */
    player.addEventListener("playStreamRejected", () => {
      if (this.player === player) this.onStatus(this.text("avatar_enable_audio"));
    });
    /** Forward valid video frame-rate measurements to page metrics. */
    player.addEventListener("statsReceived", ({ data }) => {
      if (this.player !== player) return;
      const fps = data.aggregatedStats.inboundVideoStats.framesPerSecond;
      if (typeof fps === "number" && Number.isFinite(fps)) this.onEvent({ type: "avatar_stats", fps });
    });
    player.connect();
  }

  /** Functionality: Resolve one localized player status. Inputs: i18n message key. Outputs: Localized text or the key when localization is unavailable. Constraints: English fallback keeps standalone use readable. */
  text(key) {
    const english = {
      avatar_ready: "Avatar connected",
      avatar_invalid_message: "The avatar returned an invalid message",
      avatar_controller_unresponsive: "Video connected, but the interviewer controller did not respond. Check the controller in L_Interview.",
      avatar_checking_controller: "Video connected. Checking the interviewer controller…",
      avatar_disconnected: "Avatar disconnected. Using voice/text mode.",
      avatar_connection_failed: "Avatar connection failed. Using voice/text mode.",
      avatar_enable_audio: "Select Connect / play to enable audio.",
    };
    return window.AppI18n?.t(key) ?? english[key] ?? key;
  }

  /** Functionality: Handle terminal player failure. Inputs: localized failure message. Outputs: None; releases resources and emits avatar_disconnected. Constraints: Does not retry or reconnect. */
  failed(message) {
    this.close();
    this.onStatus(message);
    this.onEvent({ type: "avatar_disconnected" });
  }

  /** Functionality: Send an interaction to UE. Inputs: JSON-compatible interaction object. Outputs: Transport result or false while unavailable. Constraints: Only sends after controller readiness. */
  send(message) {
    return this.ready && this.player?.emitUIInteraction(message);
  }

  /** Functionality: Release the current player. Inputs: None; uses instance state. Outputs: None. Logic: Clear the probe timer and destroy player resources. Constraints: Safe when no player is active. */
  close() {
    this.ready = false;
    clearInterval(this.pingTimer);
    this.pingTimer = null;
    const player = this.player;
    this.player = null;
    player?.release();
  }
}
