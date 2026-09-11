/**
 * @module stream-smoke
 * 功能：经真实本机 WebSocket 服务验证前端 StreamClient，多连接并发时逐字节核对回传。
 *
 * 目录：
 * - checkStream：
 *   单连接执行握手、不同尺寸分片、完成计数和资源清理。
 * - checkStream.object1.onProgress：
 *   累计连接进度事件，用于确认完成前已经收到校验进度。
 * - checkStream.callback1：
 *   按既定尺寸建立并发发送任务，逐项核对回传字节与进度。
 * - checkStream.callback1.callback1：
 *   由字节偏移、分片序号和客户端编号生成确定性测试载荷。
 * - checkStream.callback1.callback2：
 *   核对回传字节与原始载荷相同，并确认进度事件已到达。
 *
 * 关键变量：
 * - base：
 *   由联调启动器显式提供的 TEST_BASE_URL，缺失时立即失败。
 * - wsUrl：
 *   由 base 派生的回传 WebSocket 地址。
 *
 * 关键状态说明：
 * 每个 checkStream 独占 client，progressCount 证明完成前已有进度；sizes 固定边界尺寸，jobs
 * 并发排队到客户端，最后等待全部校验。测试不写媒体文件。
 */
import assert from "node:assert/strict";
import { StreamClient } from "../frontend/stream-client.js";

const base = process.env.TEST_BASE_URL;
assert.ok(base, "TEST_BASE_URL is required");
const wsUrl = base.replace(/^http/, "ws") + "/ws/echo/";

/** 运行单客户端用例并核对完成统计；finally 主动释放连接，不写测试载荷。 */
async function checkStream(index) {
  let progressCount = 0;
  const client = new StreamClient(wsUrl, { /** 累计连接进度事件，用于确认完成前已经收到校验进度。 */ onProgress: () => { progressCount += 1; } });
  try {
    const hello = await client.connect();
    const rtt = await client.ping();
    assert.ok(rtt >= 0);
    await client.start("binary", "application/octet-stream");
    const sizes = [1, 255, 256, 16384, 65536, 1048576];
    let bytes = 0;
    // Queue Blob conversions together: order must survive asynchronous hashing/conversion.
    const jobs = sizes.map(/** 按既定尺寸建立并发发送任务，逐项核对回传字节与进度。 */ (size, sequence) => {
      const original = Uint8Array.from({ length: size }, /** 由字节偏移、分片序号和客户端编号生成确定性测试载荷。 */ (_, offset) => (offset + sequence + index) % 256);
      bytes += size;
      return client.sendChunk(new Blob([original])).then(/** 核对回传字节与原始载荷相同，并确认进度事件已到达。 */ (echo) => {
        assert.deepEqual(new Uint8Array(echo), original);
        assert.ok(progressCount > 0, "Progress arrives while the connection is still open");
      });
    });
    await Promise.all(jobs);
    assert.equal(progressCount, sizes.length);
    const final = await client.finish();
    assert.equal(final.byte_count, bytes);
    assert.equal(final.verified_chunks, sizes.length);
    assert.equal(final.connection_id, hello.connection_id);
    assert.equal(final.status, "finished");
    console.log(`PASS frontend client ${index}: ${sizes.length} chunks, ${bytes} bytes, ping ${rtt.toFixed(2)} ms`);
  } finally { client.close(); }
}

await Promise.all([checkStream(1), checkStream(2)]);
