/**
 * @module stream-client-test
 * 功能：脱离实际网络验证 StreamClient 的载荷校验、超时和主动取消语义。
 *
 * 目录：
 * - connectedClient：
 *   创建最小连接替身，让用例只验证客户端状态逻辑。
 * - connectedClient.client.socket.close：
 *   提供不访问网络的关闭替身，使测试仅观察客户端状态。
 * - callback1：
 *   更改载荷最后一个字节，验证哈希校验拒绝且完成计数不增加。
 * - callback2：
 *   验证不存在的序号和缺少 ACK 的二进制消息不能满足等待项。
 * - callback3：
 *   伪造最终累计量，确认客户端不提前标记 finished。
 * - callback4：
 *   将测试用超时设为 10 ms，验证异常只通知一次且清理所有等待项。
 * - callback4.client.onError：
 *   累计错误通知次数，以验证超时只通知一次。
 * - callback5：
 *   主动关闭连接时，验证未完成 Promise 被拒绝，且无重连路径。
 *
 * 关键变量：
 * （无模块级变量。）
 *
 * 关键状态说明：
 * 各 test 回调创建独立 client；用例覆盖损坏载荷、错误 ACK、最终计数、超时和主动取消。只改变局部测试参数，不修改生产默认值。
 */
import assert from "node:assert/strict";
import test from "node:test";
import { StreamClient, sha256 } from "../frontend/stream-client.js";

/** 构造仅支持关闭和缓冲状态的连接替身，用于隔离测试客户端状态逻辑。 */
function connectedClient() {
  const client = new StreamClient("ws://localhost/ws/echo/");
  client.socket = { /** 提供不访问网络的关闭替身，使测试仅观察客户端状态。 */ close() {}, readyState: 1, bufferedAmount: 0 };
  return client;
}

/** 更改载荷最后一个字节，验证哈希校验拒绝且完成计数不增加。 */
test("frontend rejects corrupted echoed bytes", async () => {
  const client = connectedClient();
  const original = new Uint8Array([1, 2, 3]).buffer;
  client.pending.set(1, { hash: await sha256(original), bytes: 3, ack: true });
  const corrupted = new Uint8Array([0, 0, 0, 1, 1, 2, 4]).buffer;
  await assert.rejects(client.receive(corrupted), /SHA-256/);
  assert.equal(client.verifiedChunks, 0);
});

/** 验证不存在的序号和缺少 ACK 的二进制消息不能满足等待项。 */
test("frontend rejects missing or incorrect acknowledgement", async () => {
  const client = connectedClient();
  await assert.rejects(client.receive(JSON.stringify({ type: "ack", sequence: 99, bytes: 3, sha256: "wrong" })), /acknowledgement/);
  await assert.rejects(client.receive(new Uint8Array([0, 0, 0, 1, 1]).buffer), /SHA-256/);
});

/** 伪造最终累计量，确认客户端不提前标记 finished。 */
test("frontend rejects mismatched final counts", async () => {
  const client = connectedClient();
  await assert.rejects(client.receive(JSON.stringify({ type: "finished", chunk_count: 1, byte_count: 10 })), /counts/);
  assert.equal(client.finished, false);
});

/** 将测试用超时设为 10 ms，验证异常只通知一次且清理所有等待项。 */
test("timeouts reject pending operations and report failure", async () => {
  const client = connectedClient();
  client.timeoutMs = 10;
  let errors = 0;
  /** 累计错误通知次数，以验证超时只通知一次。 */
  client.onError = () => { errors += 1; };
  await assert.rejects(client.waitFor("pong:missing"), /Timed out/);
  assert.equal(errors, 1);
  assert.equal(client.waiters.size, 0);
});

/** 主动关闭连接时，验证未完成 Promise 被拒绝，且无重连路径。 */
test("explicit close rejects pending operations without reconnect", async () => {
  const client = connectedClient();
  const pending = client.waitFor("hello");
  client.close();
  await assert.rejects(pending, /cancelled/);
  assert.equal(client.waiters.size, 0);
});
