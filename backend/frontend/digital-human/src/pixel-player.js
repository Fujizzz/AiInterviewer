/**
 * @module pixel-player
 * Responsibilities: Wrap the official UE 5.8 pixel-streaming player; voice recognition manages the microphone separately.
 * Implementation: Validate local signalling URLs, suppress device/game input, probe controller readiness, and release the single active connection.
 * Related Modules: interview-voice.js owns speech capture and playback coordination; i18n.js provides localized status messages.
 * Declaration Index:
 * - AvatarPlayer: Manage one avatar stream connection and its control messages.
 * - AvatarPlayer.constructor: Store the container, event callbacks, and initial connection state.
 * - AvatarPlayer.text: Return localized status copy with an English fallback.
 * - AvatarPlayer.connect: Validate the local URL, configure the player, and register connection events.
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

  /** Functionality: Connect to the local signalling server. Inputs: WebSocket URL. Outputs: None; configures the player and event handlers. Logic: Reject non-local or non-ws URLs and disable microphone, camera, and game input. Constraints: Only local signalling hosts are accepted. */
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
    /** Parse UE responses and record whether the interviewer controller is ready. */
    player.addResponseEventListener("interviewer", (payload) => {
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
      let attempts = 0;
      this.pingTimer = setInterval(/** Send a ping and report when the bounded probe cannot confirm readiness. */ () => {
        player.emitUIInteraction({ type: "ping" });
        if (++attempts >= 10) {
          clearInterval(this.pingTimer);
          if (!this.ready) this.onStatus(this.text("avatar_controller_unresponsive"));
        }
      }, 500);
      this.onStatus(this.text("avatar_checking_controller"));
    });
    /** Release player state on disconnect and notify the voice interface. */
    player.addEventListener("webRtcDisconnected", () => this.failed(this.text("avatar_disconnected")));
    /** Release player state on connection failure and notify the voice interface. */
    player.addEventListener("webRtcFailed", () => this.failed(this.text("avatar_connection_failed")));
    /** Prompt the user to enable playback after autoplay is rejected. */
    player.addEventListener("playStreamRejected", () => this.onStatus(this.text("avatar_enable_audio")));
    /** Forward valid video frame-rate measurements to page metrics. */
    player.addEventListener("statsReceived", ({ data }) => {
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
    this.ready = false;
    clearInterval(this.pingTimer);
    this.onStatus(message);
    this.onEvent({ type: "avatar_disconnected" });
    const player = this.player;
    this.player = null;
    player?.disconnect();
  }

  /** Functionality: Send an interaction to UE. Inputs: JSON-compatible interaction object. Outputs: None. Constraints: Throws unless the controller has confirmed readiness. */
  send(message) {
    return this.ready && this.player?.emitUIInteraction(message);
  }

  /** Functionality: Release the current player. Inputs: None; uses instance state. Outputs: None. Logic: Clear the probe timer and destroy player resources. Constraints: Safe when no player is active. */
  close() {
    this.ready = false;
    clearInterval(this.pingTimer);
    const player = this.player;
    this.player = null;
    player?.disconnect();
  }
}
