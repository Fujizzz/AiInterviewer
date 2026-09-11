/**
 * @module app
 * 功能：流式诊断页面入口：协调 StreamClient、媒体采集模块和 DemoView，处理整次测试的生命周期。
 *
 * 目录：
 * - registerControls：
 *   保存当前媒体采集的停止与清理回调。
 * - runTest：
 *   串行执行连接、测试和结果发布，finally 解除页面运行状态。
 * - runTest.object1.onProgress：
 *   将已校验分片的进度交给视图，不自行累加计数。
 * - runTest.object1.onError：
 *   连接失败时停止当前采集，让末尾分片进入既有收尾流程。
 * - testPing：
 *   验证 ping/pong 往返并显示本地测得的延迟。
 * - testBinary：
 *   发送既定尺寸的二进制分片并核对回传。
 * - testBinary.callback1：
 *   按偏移与分片序号生成确定性字节，便于检验回传内容。
 * - testBinary.callback2：
 *   通过计时器维持既定的 50 ms 分片测试间隔。
 * - testMedia：
 *   委派媒体模块完成采集、分片回传和播放。
 * - dispose：
 *   取消活动测试并释放媒体与页面回放资源。
 * - callback1：
 *   操作映射：所有测试按钮共用 runTest 生命周期，媒体按钮仅传入明确选择的模式。
 * - callback2：
 *   启动二进制分片测试，共用 runTest 的连接与清理流程。
 * - callback3：
 *   用当前按钮的媒体模式启动统一测试流程。
 * - callback3.callback1：
 *   将本次连接和闭包中的媒体模式传给采集用例。
 * - callback4：
 *   停止保留末尾分片收尾；清空仅在空闲时执行，页面离开则立即请求资源释放。
 * - callback5：
 *   仅在没有活动测试时清除回放和页面结果。
 *
 * 关键变量：
 * - view：
 *   当前文档的 DemoView 展示实例。
 * - wsUrl：
 *   由当前页面协议与主机生成的同源回传地址。
 * - client：
 *   当前诊断连接，无活动测试时为 null。
 * - running：
 *   防止同页重复启动测试的互斥标记。
 * - stopRecording：
 *   当前录制器的停止回调。
 * - cleanupMedia：
 *   当前媒体来源的清理回调。
 *
 * 关键状态说明：
 * stream-client 负责网络协议，media 负责设备与录制，view 负责 DOM 与 Blob URL。测试参数、超时和失败语义保持既定行为。
 */
import { captureAndEcho } from "./media.js";
import { StreamClient } from "./stream-client.js";
import { DemoView } from "./view.js";

const view = new DemoView(document);
const wsUrl = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws/echo/`;
view.element("endpoint").textContent = wsUrl;
let client = null;
let running = false;
let stopRecording = null;
let cleanupMedia = null;

/** 注册当前采集器的停止与释放操作，供取消、连接错误及 pagehide 统一调用。 */
function registerControls(stop, cleanup) {
  stopRecording = stop;
  cleanupMedia = cleanup;
}

/**
 * 执行一次显式启动的测试。
 * 方法：建立连接→执行指定测试→等待 finish；finally 解除运行标志和页面引用。
 * 错误：停止网络连接并展示失败；无备用测试、隐式重试或结果持久化。
 */
async function runTest(mode, test) {
  if (running) return;
  running = true;
  view.busy(true);
  view.reset();
  view.status("连接中");
  client = new StreamClient(wsUrl, {
    /** 将已校验分片的进度交给视图，不自行累加计数。 */
    onProgress: (progress) => view.progress(progress),
    /** 连接失败时停止当前采集，让末尾分片进入既有收尾流程。 */
    onError: () => stopRecording?.(),
  });
  try {
    const hello = await client.connect();
    view.log(`连接 ${hello.connection_id} · ${mode}`);
    view.status("已连接 · 正在测试");
    await test(client);
    view.success(mode, await client.finish());
  } catch (error) {
    client.close();
    view.failure(error);
  } finally {
    client = null;
    running = false;
    view.busy(false);
  }
}

/** 发送带唯一 ID 的 ping，并用本地单调时钟测量匹配 pong 的往返时间。 */
async function testPing(connection) {
  const rtt = await connection.ping();
  view.element("latency").textContent = rtt.toFixed(1);
  view.log(`pong 已收到 · RTT ${rtt.toFixed(1)} ms`);
}

/** 按原条件发送 12 个 32 KiB 确定性载荷，分片之间保留 50 ms 测试间隔。 */
async function testBinary(connection) {
  await connection.start("binary", "application/octet-stream");
  for (let index = 0; index < 12; index += 1) {
    const payload = Uint8Array.from({ length: 32768 }, /** 按偏移与分片序号生成确定性字节，便于检验回传内容。 */ (_, offset) => (offset + index) % 256);
    await connection.sendChunk(payload.buffer);
    await new Promise(/** 通过计时器维持既定的 50 ms 分片测试间隔。 */ (resolve) => setTimeout(resolve, 50));
  }
}

/** 委派媒体采集与回传；只把完整校验后的 Blob 交给界面层，避免部分回放。 */
async function testMedia(connection, mode) {
  view.playback(await captureAndEcho(connection, mode, view, registerControls));
}

/** 页面离开时终止采集与连接，并释放回放 URL；不写入任何恢复状态。 */
function dispose() {
  stopRecording?.();
  cleanupMedia?.();
  client?.close();
  view.releasePlayback();
}

/** 操作映射：所有测试按钮共用 runTest 生命周期，媒体按钮仅传入明确选择的模式。 */
view.element("ping").onclick = () => runTest("ping", testPing);
/** 启动二进制分片测试，共用 runTest 的连接与清理流程。 */
view.element("binary").onclick = () => runTest("binary", testBinary);
for (const mode of ["synthetic", "audio", "video"]) {
  /** 用当前按钮的媒体模式启动统一测试流程。 */
  view.element(mode).onclick = () => runTest(mode, /** 将本次连接和闭包中的媒体模式传给采集用例。 */ (connection) => testMedia(connection, mode));
}
/** 停止保留末尾分片收尾；清空仅在空闲时执行，页面离开则立即请求资源释放。 */
view.element("stop").onclick = () => stopRecording?.();
/** 仅在没有活动测试时清除回放和页面结果。 */
view.element("clear").onclick = () => { if (!running) view.clear(); };
window.addEventListener("pagehide", dispose);
