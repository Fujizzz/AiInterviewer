/**
 * @module speech-tests
 * 离线语音前端回归；只使用设备和网络替身，不访问真实麦克风或云端模型。
 *
 * 目录：
 * - page：创建最小 DOM 和事件监听替身。
 * - page.globalThis.document.getElementById：按需创建并缓存控件状态。
 * - page.globalThis.window.addEventListener：忽略本测试未触发的页面事件订阅。
 * - callback1：验证分块采集与整段重采样结果一致。
 * - callback1.callback1：生成固定采样率的正弦测试输入。
 * - callback2：验证工作线程先发送小端 PCM 尾部再确认 flush，且不回放输入。
 * - callback2.globalThis.AudioWorkletProcessor：提供可记录消息的工作线程基类替身。
 * - callback2.globalThis.AudioWorkletProcessor.constructor：创建消息记录端口。
 * - callback2.globalThis.AudioWorkletProcessor.constructor.this.port.postMessage：记录工作线程发送顺序。
 * - callback2.globalThis.registerProcessor：保存被测工作线程类。
 * - callback2.callback1：验证输出缓冲区始终静音。
 * - callback2.callback2：提取消息类型以核对发送顺序。
 * - callback3：验证旧问题的 TTS 回应不会启动新一轮播放。
 * - callback3.globalThis.fetch：提供可手动结束的 TTS 请求。
 * - callback3.globalThis.fetch.callback1：保存 TTS 请求完成回调。
 * - callback3.globalThis.Audio：记录浏览器音频实例创建次数。
 * - callback3.globalThis.Audio.constructor：增加音频创建计数。
 * - callback3.object2.json：返回已经过期的问题音频。
 * - callback4：验证旧 UE 事件不改变当前轮且不会重复播放浏览器声音。
 * - callback4.voice.player.send：记录 UE 控制消息并模拟发送成功。
 * - callback4.voice.player.close：提供无需资源清理的播放器关闭替身。
 * - callback4.globalThis.fetch：提供当前问题的成功 TTS 回应。
 * - callback4.globalThis.fetch.object1.json：返回当前问题的音频标识与地址。
 * - callback4.globalThis.Audio：检测意外的浏览器重复播放。
 * - callback4.globalThis.Audio.constructor：浏览器创建重复音频时立即使测试失败。
 * - callback4.callback1：检查是否已发送 speak 消息。
 * - callback4.callback2：检查是否已发送 stop 消息。
 * - callback5：验证 TTS 配额失败后仍可启动语音回答。
 * - callback5.globalThis.fetch：提供 TTS 配额不足回应。
 * - callback5.globalThis.fetch.object1.json：返回固定公开错误信息。
 * - callback6：验证设备拒绝和取消后的迟到授权不会泄漏音轨。
 * - callback6.object1.value.mediaDevices.getUserMedia：模拟麦克风授权拒绝。
 * - callback6.callback1：提供无需处理的临时转录回调。
 * - callback6.callback2：提供无需处理的最终转录回调。
 * - callback6.callback3：提供无需处理的识别错误回调。
 * - callback6.navigator.mediaDevices.getUserMedia：提供迟到的设备授权结果。
 * - callback6.navigator.mediaDevices.getUserMedia.callback1：保存设备授权完成回调。
 * - callback6.callback4：提供取消会话的临时转录回调。
 * - callback6.callback5：提供取消会话的最终转录回调。
 * - callback6.callback6：提供取消会话的识别错误回调。
 * - callback6.object2.getTracks：返回可验证停止状态的测试音轨。
 * - callback6.object2.getTracks.object1.stop：标记迟到音轨已释放。
 * - callback7：验证登录页面提供的 CSRF token 随 TTS POST 发送。
 * - callback7.globalThis.fetch：记录请求头并返回离线供应商错误。
 * - callback7.globalThis.fetch.object1.json：返回固定公开错误，不启动音频。
 *
 * 关键变量：
 * （无模块级变量。）
 */
import test from "node:test";
import assert from "node:assert/strict";
import { PCM16Resampler } from "../../pcm-resampler.js";
import { InterviewVoice } from "../../interview-voice.js";
import { SpeechCapture } from "../../speech-capture.js";

/** 创建最小 DOM 和事件监听替身。 */
function page() {
  const elements = new Map();
  globalThis.document = { /** 按需创建并缓存控件状态。 */ getElementById(id) {
    if (!elements.has(id)) elements.set(id, { disabled: false, checked: false, textContent: "", value: "" });
    return elements.get(id);
  }};
  globalThis.window = { /** 忽略本测试未触发的页面事件订阅。 */ addEventListener() {} };
  return elements;
}

/** 验证分块采集与整段重采样结果一致。 */
test("resampling keeps exact phase across arbitrary 44.1 and 48 kHz capture blocks", () => {
  for (const rate of [44100, 48000]) {
    const input = Float32Array.from({ length: rate }, /** 生成固定采样率的正弦测试输入。 */ (_, index) => Math.sin(index * 0.13) * 0.8);
    const expected = new PCM16Resampler(rate).process(input);
    const resampler = new PCM16Resampler(rate);
    const actual = [];
    for (let offset = 0; offset < input.length; offset += 128) {
      actual.push(...resampler.process(input.subarray(offset, offset + 128)));
    }
    assert.equal(actual.length, 16000);
    assert.deepEqual(actual, expected);
  }
  assert.deepEqual(new PCM16Resampler(16000).process([2, -2, 0.5]), [32767, -32768, 16384]);
});

/** 验证工作线程先发送小端 PCM 尾部再确认 flush，且不回放输入。 */
test("worklet flush sends little-endian tail before acknowledgement and never echoes", async () => {
  const events = [];
  let Processor;
  globalThis.sampleRate = 48000;
  /** 提供可记录消息的工作线程基类替身。 */
  globalThis.AudioWorkletProcessor = class { /** 创建消息记录端口。 */ constructor() { this.port = { /** 记录工作线程发送顺序。 */ postMessage: (event) => events.push(event) }; } };
  /** 保存被测工作线程类。 */
  globalThis.registerProcessor = (_name, type) => { Processor = type; };
  await import("../../speech-worklet.js");
  const worklet = new Processor();
  const output = new Float32Array(512).fill(1);
  worklet.process([[new Float32Array(512).fill(0.5)]], [[output]]);
  assert.ok(output.every(/** 验证输出缓冲区始终静音。 */ (sample) => sample === 0));
  worklet.port.onmessage({ data: "flush" });
  assert.deepEqual(events.map(/** 提取消息类型以核对发送顺序。 */ (event) => event.type), ["pcm", "flushed"]);
  assert.equal(events[0].buffer.byteLength, 340);
  assert.equal(new DataView(events[0].buffer).getInt16(0, true), 16384);
  assert.equal(worklet.process([], [[output]]), false);
});

/** 验证旧问题的 TTS 回应不会启动新一轮播放。 */
test("late TTS result cannot start playback after the next question is displayed", async () => {
  page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Old question" };
  let resolve;
  /** 提供可手动结束的 TTS 请求。 */
  globalThis.fetch = () => new Promise(/** 保存 TTS 请求完成回调。 */ (done) => { resolve = done; });
  let audioCount = 0;
  /** 记录浏览器音频实例创建次数。 */
  globalThis.Audio = class { /** 增加音频创建计数。 */ constructor() { audioCount++; } };
  const pending = voice.speak();
  voice.setQuestion({ text: "New question" });
  resolve({ ok: true, /** 返回已经过期的问题音频。 */ json: async () => ({ utterance_id: "old", audio_url: "/old.wav" }) });
  await pending;
  assert.equal(audioCount, 0);
  assert.equal(voice.question.text, "New question");
  assert.equal(voice.busy, false);
  voice.close();
});

/** 验证旧 UE 事件不改变当前轮且不会重复播放浏览器声音。 */
test("old UE events are ignored and UE audio is never also played in the browser", async () => {
  page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Current question" };
  const sent = [];
  voice.player = { /** 记录 UE 控制消息并模拟发送成功。 */ send: (message) => { sent.push(message); return true; }, /** 提供无需资源清理的播放器关闭替身。 */ close() {} };
  /** 提供当前问题的成功 TTS 回应。 */
  globalThis.fetch = async () => ({ ok: true, /** 返回当前问题的音频标识与地址。 */ json: async () => ({ utterance_id: "current", audio_url: "/current.wav", generation_ms: 10 }) });
  /** 检测意外的浏览器重复播放。 */
  globalThis.Audio = class { /** 浏览器创建重复音频时立即使测试失败。 */ constructor() { assert.fail("Browser audio duplicated UE playback"); } };
  await voice.speak();
  voice.avatarEvent({ type: "playback_finished", utterance_id: "old" });
  assert.equal(voice.busy, true);
  voice.avatarEvent({ type: "playback_started", utterance_id: "current" });
  voice.avatarEvent({ type: "playback_finished", utterance_id: "current" });
  assert.equal(voice.busy, false);
  assert.ok(sent.some(/** 检查是否已发送 speak 消息。 */ (message) => message.type === "speak"));
  assert.ok(sent.some(/** 检查是否已发送 stop 消息。 */ (message) => message.type === "stop"));
  voice.close();
});

/** 验证 TTS 配额失败后仍可启动语音回答。 */
test("TTS failure leaves voice recording available", async () => {
  const elements = page();
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Question" };
  /** 提供 TTS 配额不足回应。 */
  globalThis.fetch = async () => ({ ok: false, /** 返回固定公开错误信息。 */ json: async () => ({ error: { code: "quota_exhausted", detail: "Use text" } }) });
  await voice.speak();
  assert.equal(elements.get("start-recording").disabled, false);
  assert.match(elements.get("voice-status").textContent, /quota_exhausted/);
  voice.close();
});

/** 验证设备拒绝和取消后的迟到授权不会泄漏音轨。 */
test("microphone refusal reports failure and late permission cannot keep a cancelled stream alive", async () => {
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    mediaDevices: { /** 模拟麦克风授权拒绝。 */ getUserMedia: async () => { throw new Error("Permission denied"); } },
  }});
  const denied = new SpeechCapture(/** 临时转录回调。 */ () => {}, /** 最终转录回调。 */ () => {}, /** 识别错误回调。 */ () => {});
  await assert.rejects(denied.start(), /Permission denied/);
  assert.equal(denied.closed, true);
  let resolve;
  let stopped = false;
  /** 提供迟到的设备授权结果。 */
  navigator.mediaDevices.getUserMedia = () => new Promise(/** 保存设备授权完成回调。 */ (done) => { resolve = done; });
  const cancelled = new SpeechCapture(/** 取消会话的临时转录回调。 */ () => {}, /** 取消会话的最终转录回调。 */ () => {}, /** 取消会话的识别错误回调。 */ () => {});
  const pending = cancelled.start();
  await cancelled.close();
  resolve({ /** 返回可验证停止状态的测试音轨。 */ getTracks: () => [{ /** 标记迟到音轨已释放。 */ stop: () => { stopped = true; } }] });
  await pending;
  assert.equal(stopped, true);
  assert.equal(cancelled.recording, false);
});

/** 新版账号接口要求 TTS 写请求携带页面 token；不削弱服务端 CSRF 检查。 */
test("TTS includes the page CSRF token for authenticated sessions", async () => {
  page();
  document.getElementById("csrf-token").content = "public-test-csrf";
  const voice = new InterviewVoice();
  voice.eligible = true;
  voice.question = { text: "Describe one contribution." };
  let headers;
  /** 保存客户端请求头；固定失败使测试不创建浏览器音频。 */
  globalThis.fetch = async (_url, options) => {
    headers = options.headers;
    return { ok: false, /** 返回离线错误，禁止真实供应商调用。 */ json: async () => ({ error: { code: "offline", detail: "Test only" } }) };
  };
  await voice.speak();
  assert.equal(headers["X-CSRFToken"], "public-test-csrf");
  assert.equal(headers["Content-Type"], "application/json");
  voice.close();
});
