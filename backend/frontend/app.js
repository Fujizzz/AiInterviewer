/**
 * @module app
 * 功能：测试页入口，协调连接、测试选择和界面生命周期。
 * 目录：registerControls；runTest；testPing；testBinary；testMedia；dispose。
 * 分层：stream-client 处理协议；media 管理采集；view 管理 DOM 和 Blob URL。
 * 约束：一次只运行一个测试，不自动重连；成功须等待服务端 finished 确认。
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
    onProgress: (progress) => view.progress(progress),
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
    const payload = Uint8Array.from({ length: 32768 }, (_, offset) => (offset + index) % 256);
    await connection.sendChunk(payload.buffer);
    await new Promise((resolve) => setTimeout(resolve, 50));
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

view.element("ping").onclick = () => runTest("ping", testPing);
view.element("binary").onclick = () => runTest("binary", testBinary);
for (const mode of ["synthetic", "audio", "video"]) {
  view.element(mode).onclick = () => runTest(mode, (connection) => testMedia(connection, mode));
}
view.element("stop").onclick = () => stopRecording?.();
view.element("clear").onclick = () => { if (!running) view.clear(); };
window.addEventListener("pagehide", dispose);
