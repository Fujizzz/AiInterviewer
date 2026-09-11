/**
 * @module stream-smoke
 * 功能：通过实际 WebSocket 服务验证前端 StreamClient 的完整回传路径。
 * 目录：checkStream；双客户端并发调度。
 * 方法：Blob 异步转换、不同尺寸载荷、逐字节校验；进度必须先于连接关闭出现。
 */
import assert from "node:assert/strict";
import { StreamClient } from "../diagnostics/web/stream-client.js";

const base = process.env.TEST_BASE_URL;
assert.ok(base, "TEST_BASE_URL is required");
const wsUrl = base.replace(/^http/, "ws") + "/ws/echo/";

/** 运行单客户端用例并核对完成统计；finally 主动释放连接，不写测试载荷。 */
async function checkStream(index) {
  let progressCount = 0;
  const client = new StreamClient(wsUrl, { onProgress: () => { progressCount += 1; } });
  try {
    const hello = await client.connect();
    const rtt = await client.ping();
    assert.ok(rtt >= 0);
    await client.start("binary", "application/octet-stream");
    const sizes = [1, 255, 256, 16384, 65536, 1048576];
    let bytes = 0;
    // Queue Blob conversions together: order must survive asynchronous hashing/conversion.
    const jobs = sizes.map((size, sequence) => {
      const original = Uint8Array.from({ length: size }, (_, offset) => (offset + sequence + index) % 256);
      bytes += size;
      return client.sendChunk(new Blob([original])).then((echo) => {
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
