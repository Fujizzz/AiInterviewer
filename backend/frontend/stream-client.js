/**
 * @module stream-client
 * 功能：供浏览器与 Node 测试共用的严格 WebSocket 回传客户端。
 * 目录：StreamClient；sha256。
 * StreamClient：constructor、waitFor、request、connect、sendJSON、ping、start、
 * sendChunk、finish、receive、rejectPending、fail、close。
 * 方法：双 Promise 队列保持发送/接收处理顺序，关联表核对 ACK 与二进制回传。
 * 约束：只有完成哈希校验才累计进度；不执行隐式重连或重传。
 */
export class StreamClient {
  /** 初始化单连接状态与回调；timeoutMs 保持既定 10 秒，构造不发起网络请求。 */
  constructor(url, { onProgress = () => {}, onError = () => {}, timeoutMs = 10000 } = {}) {
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
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new Error(`Timed out waiting for ${key}`)), this.timeoutMs);
      this.waiters.set(key, {
        resolve: (value) => { clearTimeout(timer); this.waiters.delete(key); resolve(value); },
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
    this.socket.onmessage = (event) => {
      this.receiveQueue = this.receiveQueue.then(() => this.receive(event.data)).catch((error) => this.fail(error));
    };
    this.socket.onerror = () => this.fail(new Error("WebSocket connection failed. Check the ASGI server."));
    this.socket.onclose = (event) => {
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
    const task = this.sendQueue.then(async () => {
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
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", buffer))].map((b) => b.toString(16).padStart(2, "0")).join("");
}
