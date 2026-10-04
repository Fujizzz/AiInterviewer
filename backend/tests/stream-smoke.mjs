/**
 * @module stream-smoke
 * Purpose: Validate frontend StreamClient using real local WebSocket service, verifying returned bytes per byte under concurrent multiple connections.
 *
 * Declaration Index:
 * - checkStream:
 *   Execute handshake, different-sized chunks, completion counting, and resource cleanup on single connection.
 * - checkStream.object1.onProgress:
 *   Accumulate connection progress events to confirm verification progress was received before completion.
 * - checkStream.callback1:
 *   Establish concurrent send tasks with predefined sizes, verifying returned bytes and progress per item.
 * - checkStream.callback1.callback1:
 *   Generate deterministic test payload from byte offset, chunk sequence, and client ID.
 * - checkStream.callback1.callback2:
 *   Verify returned bytes match original payload and confirm progress events have arrived.
 *
 * Variable Index:
 * - base:
 *   TEST_BASE_URL explicitly provided by integration launcher; failure occurs immediately if missing.
 * - wsUrl:
 *   WebSocket URL derived from base.
 *
 * Key State Explanation:
 * Each checkStream uses a dedicated client; progressCount confirms progress was received before completion; sizes use fixed boundary values, jobs are queued concurrently to the client, and final verification waits for all. No media files are written during testing.
 */
import assert from "node:assert/strict";
import { StreamClient } from "../frontend/stream-client.js";

const base = process.env.TEST_BASE_URL;
assert.ok(base, "TEST_BASE_URL is required");
const wsUrl = base.replace(/^http/, "ws") + "/ws/echo/";

/**
 *  Run single-client test case and verify completion statistics; finally, actively release connection without writing test payload.
 */
async function checkStream(index) {
  let progressCount = 0;
  const client = new StreamClient(wsUrl, { /**
 *  Accumulate connection progress events to confirm verification progress was received before completion.
 */ onProgress: () => { progressCount += 1; } });
  try {
    const hello = await client.connect();
    const rtt = await client.ping();
    assert.ok(rtt >= 0);
    await client.start("binary", "application/octet-stream");
    const sizes = [1, 255, 256, 16384, 65536, 1048576];
    let bytes = 0;
    // Queue Blob conversions together: order must survive asynchronous hashing/conversion.
    const jobs = sizes.map(/**
 *  Establish concurrent send tasks with predefined sizes, verifying returned bytes and progress per item.
 */ (size, sequence) => {
      const original = Uint8Array.from({ length: size }, /**
 *  Generate deterministic test payload from byte offset, chunk sequence, and client ID.
 */ (_, offset) => (offset + sequence + index) % 256);
      bytes += size;
      return client.sendChunk(new Blob([original])).then(/**
 *  Verify returned bytes match original payload and confirm progress events have arrived.
 */ (echo) => {
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
