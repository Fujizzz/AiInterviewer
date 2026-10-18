/**
 * @module stream-client-test
 * Purpose: Verify StreamClient's payload validation, timeout, and active cancellation semantics without actual network.
 *
 * Declaration Index:
 * - connectedClient:
 *   Create a minimal connected stub to test client state logic only.
 * - connectedClient.client.socket.close:
 *   Provide a no-network-access close stub so tests observe only client state.
 * - callback1:
 *   Modify the last byte of the payload to verify hash check rejection and that completion count does not increase.
 * - callback2:
 *   Verify that messages with non-existent sequence numbers and missing ACKs cannot fulfill pending items.
 * - callback3:
 *   Forge final cumulative value to confirm the client does not prematurely mark finished.
 * - callback4:
 *   Set test timeout to 10 ms to verify exceptions are notified only once and all pending items are cleaned up.
 * - callback4.client.onError:
 *   Count error notifications to verify timeout triggers only once.
 * - callback5:
 *   When the connection is closed explicitly, verify uncompleted Promises are rejected and there is no reconnection path.
 *
 * Variable Index:
 * None
 *
 * Key State Explanation:
 * Each test callback creates an independent client; test cases cover corrupted payloads, invalid ACKs, final counts, timeouts, and active cancellation. Only local test parameters are changed, production defaults remain unchanged.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { StreamClient, sha256 } from "../frontend/stream-client.js";

/**
 *  Construct a connection stub supporting only close and buffer states for isolating client state logic testing.
 */
function connectedClient() {
  const client = new StreamClient("ws://localhost/ws/echo/");
  client.socket = { /**
 *  Provide a no-network-access close stub so tests observe only client state.
 */ close() {}, readyState: 1, bufferedAmount: 0 };
  return client;
}

/**
 *  Modify the last byte of the payload to verify hash check rejection and that completion count does not increase.
 */
test("frontend rejects corrupted echoed bytes", async () => {
  const client = connectedClient();
  const original = new Uint8Array([1, 2, 3]).buffer;
  client.pending.set(1, { hash: await sha256(original), bytes: 3, ack: true });
  const corrupted = new Uint8Array([0, 0, 0, 1, 1, 2, 4]).buffer;
  await assert.rejects(client.receive(corrupted), /SHA-256/);
  assert.equal(client.verifiedChunks, 0);
});

/**
 *  Verify that binary messages with non-existent sequence numbers and missing ACKs cannot satisfy pending items.
 */
test("frontend rejects missing or incorrect acknowledgement", async () => {
  const client = connectedClient();
  await assert.rejects(client.receive(JSON.stringify({ type: "ack", sequence: 99, bytes: 3, sha256: "wrong" })), /acknowledgement/);
  await assert.rejects(client.receive(new Uint8Array([0, 0, 0, 1, 1]).buffer), /SHA-256/);
});

/**
 *  Forge final cumulative value to confirm the client does not prematurely mark finished.
 */
test("frontend rejects mismatched final counts", async () => {
  const client = connectedClient();
  await assert.rejects(client.receive(JSON.stringify({ type: "finished", chunk_count: 1, byte_count: 10 })), /counts/);
  assert.equal(client.finished, false);
});

/**
 *  Set test timeout to 10 ms to verify exceptions are notified only once and all pending items are cleaned up.
 */
test("timeouts reject pending operations and report failure", async () => {
  const client = connectedClient();
  client.timeoutMs = 10;
  let errors = 0;
  /**
 *  Count error notifications to verify timeout triggers only once.
 */
  client.onError = () => { errors += 1; };
  await assert.rejects(client.waitFor("pong:missing"), /Timed out/);
  assert.equal(errors, 1);
  assert.equal(client.waiters.size, 0);
});

/**
 * Verify that explicitly closing a connection rejects unfinished Promises without a reconnection path.
 */
test("explicit close rejects pending operations without reconnect", async () => {
  const client = connectedClient();
  const pending = client.waitFor("hello");
  client.close();
  await assert.rejects(pending, /cancelled/);
  assert.equal(client.waiters.size, 0);
});
