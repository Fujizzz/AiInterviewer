/**
 * @module interview-camera-test
 * 职责：以 DOM、媒体轨道和权限 Promise 替身验证本地摄像头的资源生命周期。
 * 实现：vm 执行实际客户端；覆盖显式启停、拒绝、迟到授权、页面离开和设备终止。
 * 关联：frontend/interview-camera.js；测试不接触摄像头或网络，不证明真实硬件可用。
 * 目录：
 * - Element：最小 DOM 和媒体播放替身。
 * - Element.constructor：初始化显示属性及事件集合。
 * - Element.addEventListener：记录元素事件。
 * - Element.setAttribute：记录可访问属性。
 * - Element.play：模拟成功或失败的播放 Promise。
 * - Track：可观察的媒体轨道。
 * - Track.constructor：初始化停止次数和监听器。
 * - Track.addEventListener：登记设备结束处理器。
 * - Track.removeEventListener：移除设备结束处理器。
 * - Track.stop：累计停止次数，不模拟外部 ended 事件。
 * - Stream：包含一条视频轨道的媒体替身。
 * - Stream.constructor：创建可观察轨道。
 * - Stream.getTracks：返回所有轨道。
 * - Stream.getVideoTracks：返回视频轨道。
 * - makePage：建立独立脚本上下文和可控授权。
 * - makePage.authorize：记录申请约束并返回测试指定的 Promise。
 * - makePage.getElement：严格查找实际模板元素。
 * - makePage.listen：登记页面生命周期事件。
 * - makeDeferred：构造可显式完成的权限 Promise。
 * - makeDeferred.executor：捕获 resolve 方法供测试控制授权顺序。
 * - explicitPreview：验证加载不申请设备、只申请视频及关闭释放。
 * - rejectedPermission：验证权限拒绝的错误展示及无重试。
 * - cancelledPermission：验证取消后的迟到授权不会打开预览。
 * - pageExit：验证离开时停止活动轨道。
 * - pendingPageExit：验证离开后的迟到授权仅释放。
 * - endedDevice：验证设备终止后清理与明确提示。
 * - failedPlayback：验证播放失败后释放已取得的媒体。
 * 关键变量：
 * - SCRIPT：真实摄像头客户端源码。
 * - HTML：实际模板，用于严格校验元素引用。
 * 约束：
 * 不调用模型、不使用真实设备；Stream、Element 的状态仅为确定性测试替身。
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { Console } from "node:console";
import { PassThrough } from "node:stream";

const SCRIPT = readFileSync(new URL("../frontend/interview-camera.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../frontend/agent.html", import.meta.url), "utf8");

/** 功能：替代页面元素。逻辑：记录脚本状态，不模拟浏览器布局或设备。 */
class Element {
  /** 输入无；初始化 DOM 属性、事件集合及可注入的播放异常。 */
  constructor() { this.hidden = false; this.srcObject = null; this.textContent = ""; this.events = {}; this.attributes = {}; this.playError = null; }
  /** 输入事件名及处理器；保存引用，输出无。 */
  addEventListener(name, handler) { this.events[name] = handler; }
  /** 输入属性名和值；记录状态用于断言，输出无。 */
  setAttribute(name, value) { this.attributes[name] = value; }
  /** 读取测试注入的 playError；成功返回已完成 Promise，否则抛出模拟播放异常。 */
  async play() { if (this.playError) throw this.playError; }
}

/** 功能：轨道替身。逻辑：记录释放次数，设备 ended 由测试显式触发。 */
class Track {
  /** 输入无；初始化停止次数和监听器。 */
  constructor() { this.stops = 0; this.events = {}; }
  /** 输入事件名和处理器；保存监听器，输出无。 */
  addEventListener(name, handler) { this.events[name] = handler; }
  /** 输入事件名及处理器；仅移除匹配监听器，输出无。 */
  removeEventListener(name, handler) { if (this.events[name] === handler) delete this.events[name]; }
  /** 输入无；累计停止次数，不派发 ended，遵循主动 stop 不触发该事件的接口行为。 */
  stop() { this.stops += 1; }
}

/** 功能：模拟单视频轨道流；不包含任何真实音视频内容。 */
class Stream {
  /** 输入无；构造唯一 Track 实例。 */
  constructor() { this.track = new Track(); }
  /** 输入无；返回所有模拟轨道，供资源释放逻辑调用。 */
  getTracks() { return [this.track]; }
  /** 输入无；返回视频轨道，供 ended 监听器注册。 */
  getVideoTracks() { return [this.track]; }
}

/** 输入无；创建隔离客户端上下文，返回元素、授权控制及生命周期事件；无网络或真实设备副作用。 */
function makePage() {
  const elements = new Map();
  for (const match of HTML.matchAll(/id="([^"]+)"/g)) {
    assert.ok(!elements.has(match[1]), `Duplicate DOM ID: ${match[1]}`);
    elements.set(match[1], new Element());
  }
  const lifecycle = {};
  const media = { calls: [], result: null };
  /** 输入 getUserMedia 约束；记录调用并返回测试注入的 Promise，不申请真实权限。 */
  function authorize(options) { media.calls.push(options); return media.result; }
  /** 输入 ID；返回真实模板对应的替身，缺失立即失败。 */
  function getElement(id) { assert.ok(elements.has(id), id); return elements.get(id); }
  /** 输入页面事件和处理器；保存供测试触发，不注册真实窗口事件。 */
  function listen(name, handler) { lifecycle[name] = handler; }
  vm.runInNewContext(SCRIPT, {
    document: { getElementById: getElement }, window: { addEventListener: listen },
    navigator: { mediaDevices: { getUserMedia: authorize } },
    console: new Console(new PassThrough()),
  });
  return { elements, media, lifecycle, click: getElement("camera-toggle").events.click };
}

/** 输入无；返回 Promise 和其 resolve 方法，模拟用户延迟授予摄像头权限。 */
function makeDeferred() {
  let resolve;
  /** 输入 Promise 的 resolve 方法；保存到外层，输出无。 */
  function executor(accept) { resolve = accept; }
  const promise = new Promise(executor);
  return { promise, resolve };
}

/** 前提：模拟设备可用；验证显式申请、audio=false、播放和关闭释放，不代表真实摄像头验证。 */
async function explicitPreview() {
  const page = makePage();
  const stream = new Stream();
  assert.equal(page.media.calls.length, 0);
  page.media.result = Promise.resolve(stream);
  await page.click();
  assert.equal(page.media.calls.length, 1);
  assert.equal(page.media.calls[0].audio, false);
  assert.equal(page.media.calls[0].video, true);
  assert.equal(page.elements.get("self-video").srcObject, stream);
  assert.equal(page.elements.get("camera-toggle").attributes["aria-pressed"], "true");
  await page.click();
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
  assert.equal(page.elements.get("self-video").hidden, true);
}

/** 前提：权限 Promise 拒绝；验证错误被明确展示且未重复申请。 */
async function rejectedPermission() {
  const page = makePage();
  page.media.result = Promise.reject(Object.assign(new Error("denied"), { name: "NotAllowedError" }));
  await page.click();
  assert.match(page.elements.get("camera-status").textContent, /权限被拒绝/);
  assert.equal(page.media.calls.length, 1);
  assert.equal(page.elements.get("camera-toggle").textContent, "开启摄像头");
}

/** 前提：授权尚未返回即取消；验证迟到流被停止且预览保持关闭。 */
async function cancelledPermission() {
  const page = makePage();
  const deferred = makeDeferred();
  const stream = new Stream();
  page.media.result = deferred.promise;
  const opening = page.click();
  await page.click();
  deferred.resolve(stream);
  await opening;
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
  assert.equal(page.elements.get("camera-toggle").attributes["aria-pressed"], "false");
}

/** 前提：正在本地播放；验证 pagehide 释放轨道并移除设备事件监听器。 */
async function pageExit() {
  const page = makePage();
  const stream = new Stream();
  page.media.result = Promise.resolve(stream);
  await page.click();
  page.lifecycle.pagehide();
  assert.equal(stream.track.stops, 1);
  assert.equal(stream.track.events.ended, undefined);
  assert.equal(page.elements.get("self-video").srcObject, null);
}

/** 前提：页面离开后授权才返回；验证迟到媒体被释放，不能更新页面为播放状态。 */
async function pendingPageExit() {
  const page = makePage();
  const deferred = makeDeferred();
  const stream = new Stream();
  page.media.result = deferred.promise;
  const opening = page.click();
  page.lifecycle.pagehide();
  deferred.resolve(stream);
  await opening;
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
}

/** 前提：活动设备主动发出 ended；验证结束提示、资源释放及无自动重启。 */
async function endedDevice() {
  const page = makePage();
  const stream = new Stream();
  page.media.result = Promise.resolve(stream);
  await page.click();
  stream.track.events.ended();
  assert.equal(stream.track.stops, 1);
  assert.match(page.elements.get("camera-status").textContent, /连接已中断/);
  assert.equal(page.media.calls.length, 1);
}

/** 前提：已拿到流但 video.play 拒绝；验证错误不会遗留活动轨道。 */
async function failedPlayback() {
  const page = makePage();
  const stream = new Stream();
  page.media.result = Promise.resolve(stream);
  page.elements.get("self-video").playError = new Error("play failed");
  await page.click();
  assert.equal(stream.track.stops, 1);
  assert.equal(page.elements.get("self-video").srcObject, null);
  assert.match(page.elements.get("camera-status").textContent, /开启失败/);
}

test("preview is explicit, video-only, and releases on close", explicitPreview);
test("permission denial is visible without automatic retry", rejectedPermission);
test("cancel releases a late permission result", cancelledPermission);
test("pagehide releases the active camera", pageExit);
test("pagehide invalidates pending permission", pendingPageExit);
test("device ended clears preview without restarting", endedDevice);
test("playback failure releases acquired tracks", failedPlayback);
