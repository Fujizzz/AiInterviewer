/**
 * @module stream-client
 * 功能：浏览器与 Node 共用的 WebSocket 回传客户端：关联控制消息、验证 ACK 和二进制哈希，并统一传播失败。
 *
 * 目录：
 * - StreamClient：
 *   管理单连接协议状态、异步队列、载荷校验与取消清理。
 * - StreamClient.constructor：
 *   初始化单连接状态与回调；timeoutMs 保持既定 10 秒，构造不发起网络请求。
 * - StreamClient.constructor.callback1：
 *   调用方未订阅进度时不执行额外操作，协议计数仍由客户端维护。
 * - StreamClient.constructor.callback2：
 *   调用方未提供错误通知时不执行额外操作，失败仍通过等待项传播。
 * - StreamClient.waitFor：
 *   注册唯一响应等待项并启动超时计时器。
 * - StreamClient.waitFor.callback1：
 *   创建带超时的等待项，并包装成功与失败路径以统一释放计时器。
 * - StreamClient.waitFor.callback1.callback1：
 *   等待超时后进入统一失败流程，拒绝所有未完成操作。
 * - StreamClient.waitFor.callback1.object1.resolve：
 *   清除计时器并移除等待项，然后兑现对应 Promise。
 * - StreamClient.waitFor.callback1.object1.reject：
 *   清除计时器并移除等待项，然后拒绝对应 Promise。
 * - StreamClient.request：
 *   将“注册等待→发送控制消息→传播发送失败”合并为单一路径，防止漏清理。
 * - StreamClient.connect：
 *   建立一次连接并等待 hello 公告。
 * - StreamClient.connect.this.socket.onmessage：
 *   将当前消息接入接收队列，保持解析与状态更新的顺序。
 * - StreamClient.connect.this.socket.onmessage.callback1：
 *   前序消息处理完成后解析当前消息，避免并发校验打乱状态。
 * - StreamClient.connect.this.socket.onmessage.callback2：
 *   把接收队列中的异常交给统一失败和资源清理流程。
 * - StreamClient.connect.this.socket.onerror：
 *   把传输错误转换为明确的连接失败，不建立重连路径。
 * - StreamClient.connect.this.socket.onclose：
 *   等待已收到的消息处理完毕，再判断是否为提前断线。
 * - StreamClient.connect.this.socket.onclose.callback1：
 *   接收队列结束后，仅对未完成且未取消的连接报告异常关闭。
 * - StreamClient.sendJSON：
 *   编码一条控制消息；失败状态或未连接时立即抛错，不缓冲等待恢复。
 * - StreamClient.ping：
 *   使用唯一 ID 关联 pong，以 performance.now 测量本端 RTT，不依赖服务端时钟。
 * - StreamClient.start：
 *   声明当前连接的模式与 MIME 类型，Promise 在服务端 started 确认后完成。
 * - StreamClient.sendChunk：
 *   排队发送一个 Blob 或 ArrayBuffer，并等待其回传被验证。
 * - StreamClient.sendChunk.callback1：
 *   按发送队列顺序校验容量、生成帧并等待对应回传验证。
 * - StreamClient.finish：
 *   等待所有分片 Promise 完成后提交校验计数，避免提前结束而丢失末尾媒体。
 * - StreamClient.receive：
 *   校验控制消息或二进制回传，再解析对应等待项。
 * - StreamClient.rejectPending：
 *   统一拒绝未完成等待项，并清除分片元数据；调用者决定连接关闭原因。
 * - StreamClient.fail：
 *   传播第一次失败，关闭连接并通知 UI；后续重复异常不覆盖原始失败原因。
 * - StreamClient.close：
 *   主动取消连接和全部等待，不进行重连；用于页面离开或测试结束后的释放。
 * - sha256：
 *   计算 ArrayBuffer 的 SHA-256 十六进制摘要。
 * - sha256.callback1：
 *   将每个摘要字节编码为补足两位的十六进制文本。
 *
 * 关键变量：
 * （无模块级变量。）
 *
 * 关键状态说明：
 * 实例的 waiters 保存控制/分片 Promise，pending 保存待校验哈希与计数；sendQueue/receiveQueue
 * 保持顺序。sequence/sentBytes 记录发送进度，verifiedChunks/verifiedBytes
 * 只在验证后增长；failure/finished/cancelled 区分失败、成功与主动取消。
 */
/** 管理单连接协议状态、异步队列、载荷校验与取消清理。 */
export class StreamClient {
  /** 初始化单连接状态与回调；timeoutMs 保持既定 10 秒，构造不发起网络请求。 */
  constructor(url, { onProgress = /** 调用方未订阅进度时不执行额外操作，协议计数仍由客户端维护。 */ () => {}, onError = /** 调用方未提供错误通知时不执行额外操作，失败仍通过等待项传播。 */ () => {}, timeoutMs = 10000 } = {}) {
    this.url = url;
    this.onProgress = onProgress;
    this.onError = onError;
    this.timeoutMs = timeoutMs;
    this.pending = new Map();
    this.waiters = new Map();
    this.sequence = 0;
    this.verifiedBytes = 0;
    this.verifiedChunks = 0;
    this.sentBytes = 0;
    this.sendQueue = Promise.resolve();
    this.receiveQueue = Promise.resolve();
    this.failure = null;
    this.finished = false;
  }

  /**
   * 注册唯一响应等待项并启动超时计时器。
   * 返回：匹配响应到达时完成的 Promise；成功或失败都清除计时器与关联项。
   * 相同 key 的并发等待显式报错，避免一个响应错误满足多个请求。
   */
  waitFor(key) {
    if (this.waiters.has(key)) throw new Error(`Already waiting for ${key}`);
    return new Promise(/** 创建带超时的等待项，并包装成功与失败路径以统一释放计时器。 */ (resolve, reject) => {
      const timer = setTimeout(/** 等待超时后进入统一失败流程，拒绝所有未完成操作。 */ () => this.fail(new Error(`Timed out waiting for ${key}`)), this.timeoutMs);
      this.waiters.set(key, {
        /** 清除计时器并移除等待项，然后兑现对应 Promise。 */
        resolve: (value) => { clearTimeout(timer); this.waiters.delete(key); resolve(value); },
        /** 清除计时器并移除等待项，然后拒绝对应 Promise。 */
        reject: (error) => { clearTimeout(timer); this.waiters.delete(key); reject(error); },
      });
    });
  }

  /** 将“注册等待→发送控制消息→传播发送失败”合并为单一路径，防止漏清理。 */
  request(key, data) {
    const result = this.waitFor(key);
    try { this.sendJSON(data); } catch (error) { this.fail(error); }
    return result;
  }

  /**
   * 建立一次连接并等待 hello 公告。
   * 方法：二进制解码为 ArrayBuffer，消息按 receiveQueue 串行校验。
   * 异常关闭先等待已有接收任务收尾，再判断是否缺失正常 finished 确认。
   */
  async connect() {
    if (this.socket) throw new Error("Use a new client for each test.");
    const ready = this.waitFor("hello");
    this.socket = new WebSocket(this.url);
    this.socket.binaryType = "arraybuffer";
    /** 将当前消息接入接收队列，保持解析与状态更新的顺序。 */
    this.socket.onmessage = (event) => {
      this.receiveQueue = this.receiveQueue.then(/** 前序消息处理完成后解析当前消息，避免并发校验打乱状态。 */ () => this.receive(event.data)).catch(/** 把接收队列中的异常交给统一失败和资源清理流程。 */ (error) => this.fail(error));
    };
    /** 把传输错误转换为明确的连接失败，不建立重连路径。 */
    this.socket.onerror = () => this.fail(new Error("WebSocket connection failed. Check the ASGI server."));
    /** 等待已收到的消息处理完毕，再判断是否为提前断线。 */
    this.socket.onclose = (event) => {
      /** 接收队列结束后，仅对未完成且未取消的连接报告异常关闭。 */
      this.receiveQueue.then(() => {
        if (!this.finished && !this.cancelled) this.fail(new Error(`Connection closed before finish (code ${event.code}).`));
      });
    };
    this.hello = await ready;
    return this.hello;
  }

  /** 编码一条控制消息；失败状态或未连接时立即抛错，不缓冲等待恢复。 */
  sendJSON(data) {
    if (this.failure) throw this.failure;
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error("WebSocket is not connected.");
    this.socket.send(JSON.stringify(data));
  }

  /** 使用唯一 ID 关联 pong，以 performance.now 测量本端 RTT，不依赖服务端时钟。 */
  async ping() {
    const id = crypto.randomUUID();
    const started = performance.now();
    await this.request(`pong:${id}`, { type: "ping", id });
    return performance.now() - started;
  }

  /** 声明当前连接的模式与 MIME 类型，Promise 在服务端 started 确认后完成。 */
  async start(mode, mimeType) {
    return this.request("started", { type: "start", mode, mime_type: mimeType });
  }

  /**
   * 排队发送一个 Blob 或 ArrayBuffer，并等待其回传被验证。
   * 方法：按序转换 Blob、检查服务端容量、计算哈希、编码大端序号头并发送。
   * 返回：净载荷 ArrayBuffer 的 Promise；大小或背压超限明确失败，不丢帧。
   */
  sendChunk(payload) {
    const task = this.sendQueue.then(/** 按发送队列顺序校验容量、生成帧并等待对应回传验证。 */ async () => {
      if (this.failure) throw this.failure;
      const buffer = payload instanceof Blob ? await payload.arrayBuffer() : payload;
      if (!(buffer instanceof ArrayBuffer) || buffer.byteLength === 0) throw new Error("Expected a nonempty ArrayBuffer or Blob.");
      if (buffer.byteLength > this.hello.max_chunk_bytes || this.sentBytes + buffer.byteLength > this.hello.max_total_bytes) {
        throw new Error("Media payload exceeds the server's advertised size limit.");
      }
      if (this.socket.bufferedAmount > this.hello.max_chunk_bytes) throw new Error("WebSocket send buffer is full; test stopped.");
      const sequence = ++this.sequence;
      const hash = await sha256(buffer);
      const frame = new ArrayBuffer(buffer.byteLength + 4);
      new DataView(frame).setUint32(0, sequence);
      new Uint8Array(frame, 4).set(new Uint8Array(buffer));
      const verified = this.waitFor(`chunk:${sequence}`);
      this.pending.set(sequence, { hash, bytes: buffer.byteLength, started: performance.now() });
      this.sentBytes += buffer.byteLength;
      try {
        if (this.socket.readyState !== WebSocket.OPEN) throw new Error("Connection closed before sending a chunk.");
        this.socket.send(frame);
      } catch (error) { this.fail(error); }
      return verified;
    });
    this.sendQueue = task;
    return task;
  }

  /** 等待所有分片 Promise 完成后提交校验计数，避免提前结束而丢失末尾媒体。 */
  async finish() {
    await this.sendQueue;
    if (this.failure) throw this.failure;
    return this.request("finished", {
      type: "finish", verified_chunks: this.verifiedChunks, verified_bytes: this.verifiedBytes,
    });
  }

  /**
   * 校验控制消息或二进制回传，再解析对应等待项。
   * 方法：ACK 必须匹配本地哈希；二进制必须有先前 ACK 且哈希一致。
   * 完成计数只依据已验证载荷更新；未知、乱序或损坏响应抛错交由 fail 收尾。
   */
  async receive(data) {
    if (typeof data === "string") {
      const message = JSON.parse(data);
      if (message.type === "error") throw new Error(`${message.code}: ${message.detail}`);
      if (message.type === "ack") {
        const item = this.pending.get(message.sequence);
        if (!item || item.ack || message.bytes !== item.bytes || message.sha256 !== item.hash) throw new Error("Invalid chunk acknowledgement.");
        item.ack = true;
        return;
      }
      if (message.type === "finished") {
        if (message.chunk_count !== this.verifiedChunks || message.byte_count !== this.verifiedBytes) throw new Error("Final server counts differ from verified data.");
        this.finished = true;
      }
      const key = message.type === "pong" ? `pong:${message.id}` : message.type;
      const waiter = this.waiters.get(key);
      if (!waiter) throw new Error(`Unexpected server message: ${message.type}`);
      waiter.resolve(message);
      return;
    }
    if (!(data instanceof ArrayBuffer) || data.byteLength < 5) throw new Error("Invalid echoed binary frame.");
    const sequence = new DataView(data).getUint32(0);
    const item = this.pending.get(sequence);
    const payload = data.slice(4);
    if (!item?.ack || payload.byteLength !== item.bytes || await sha256(payload) !== item.hash) throw new Error("Echoed payload failed SHA-256 verification.");
    this.pending.delete(sequence);
    this.verifiedChunks += 1;
    this.verifiedBytes += payload.byteLength;
    const progress = { sequence, bytes: this.verifiedBytes, chunks: this.verifiedChunks, rttMs: performance.now() - item.started };
    this.waiters.get(`chunk:${sequence}`).resolve(payload);
    this.onProgress(progress);
  }

  /** 统一拒绝未完成等待项，并清除分片元数据；调用者决定连接关闭原因。 */
  rejectPending(error) {
    for (const waiter of [...this.waiters.values()]) waiter.reject(error);
    this.pending.clear();
  }

  /** 传播第一次失败，关闭连接并通知 UI；后续重复异常不覆盖原始失败原因。 */
  fail(error) {
    if (this.failure || this.finished || this.cancelled) return;
    this.failure = error;
    this.rejectPending(error);
    this.socket?.close(1000, "Test failed");
    this.onError(error);
  }

  /** 主动取消连接和全部等待，不进行重连；用于页面离开或测试结束后的释放。 */
  close() {
    this.cancelled = true;
    const error = new Error("Test cancelled by client.");
    this.failure = error;
    this.rejectPending(error);
    this.socket?.close(1000, "Client closed");
  }
}

/** 计算输入缓冲区的 SHA-256，并返回固定 64 字符的小写十六进制字符串。 */
export async function sha256(buffer) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", buffer))].map(/** 将每个摘要字节编码为补足两位的十六进制文本。 */ (b) => b.toString(16).padStart(2, "0")).join("");
}
