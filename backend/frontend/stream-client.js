/**
 *
 * @module stream-client
 * Responsibilities: Provide the browser and Node WebSocket client used by the media-stream protocol tests.
 * Implementation: Associate control responses with pending requests, validate acknowledgements and binary payload hashes, and propagate failures through pending operations.
 * Related Modules: Imported by browser stream capture and Node protocol tests; communicates with the configured streaming WebSocket endpoint.
 *
 * Declaration Index:
 * - StreamClient:
 *   Manage single connection protocol state, asynchronous queues, payload validation, and cancellation cleanup.
 * - StreamClient.constructor:
 *   Initialize single connection state and callbacks; timeoutMs defaults to 10 seconds but can be configured; construction does not initiate network requests.
 * - StreamClient.constructor.callback1:
 *   Perform no additional actions if caller does not subscribe to progress; protocol counting is still maintained by the client.
 * - StreamClient.constructor.callback2:
 *   Perform no additional actions if caller does not provide error notification; failures still propagate through pending items.
 * - StreamClient.waitFor:
 *   Register a unique response wait item and start timeout timer.
 * - StreamClient.waitFor.callback1:
 *   Create a timed wait item and wrap success and failure paths to uniformly release the timer.
 * - StreamClient.waitFor.callback1.callback1:
 *   Enter unified failure flow after timeout, rejecting all incomplete operations.
 * - StreamClient.waitFor.callback1.object1.resolve:
 *   Clear timer, remove wait item, then fulfill corresponding Promise.
 * - StreamClient.waitFor.callback1.object1.reject:
 *   Clear timer, remove wait item, then reject corresponding Promise.
 * - StreamClient.request:
 *   Merge "register wait → send control message → propagate send failure" into a single path to prevent missed cleanup.
 * - StreamClient.connect:
 *   Establish a single connection and wait for hello announcement.
 * - StreamClient.connect.this.socket.onmessage:
 *   Feed current message into receive queue, maintaining order of parsing and state updates.
 * - StreamClient.connect.this.socket.onmessage.callback1:
 *   Parse current message only after prior message processing completes, avoiding concurrent validation disrupting state.
 * - StreamClient.connect.this.socket.onmessage.callback2:
 *   Pass exceptions from receive queue to unified failure and resource cleanup process.
 * - StreamClient.connect.this.socket.onerror:
 *   Convert transmission errors into explicit connection failure; no reconnection path established.
 * - StreamClient.connect.this.socket.onclose:
 *   Wait for all received messages to be processed before determining if disconnection was premature.
 * - StreamClient.connect.this.socket.onclose.callback1:
 *   After receive queue ends, report abnormal closure only for incomplete and uncanceled connections.
 * - StreamClient.sendJSON:
 *   Encode a control message; throw immediately on failure state or disconnected state, without buffering for recovery.
 * - StreamClient.ping:
 *   Use unique ID to associate pong, measure RTT using performance.now, without relying on server clock.
 * - StreamClient.start:
 *   Declare current connection mode and MIME type; Promise resolves after server confirms started.
 * - StreamClient.sendChunk:
 *   Queue a Blob or ArrayBuffer for sending, and wait for its return verification.
 * - StreamClient.sendChunk.callback1:
 *   Validate capacity, generate frames, and wait for corresponding return verification in send queue order.
 * - StreamClient.finish:
 *   Wait for all chunk Promises to complete before submitting verification count, preventing premature end and loss of trailing media.
 * - StreamClient.receive:
 *   Validate control message or binary return, then parse corresponding wait item.
 * - StreamClient.rejectPending:
 *   Uniformly reject incomplete wait items and clear chunk metadata; caller decides connection close reason.
 * - StreamClient.fail:
 *   Propagate first failure, close connection, and notify UI; subsequent repeated exceptions do not overwrite original failure cause.
 * - StreamClient.close:
 *   Actively cancel connection and all waits, without attempting reconnection; used for page leave or test end cleanup.
 * - sha256:
 *   Compute SHA-256 hexadecimal digest of ArrayBuffer.
 * - sha256.callback1:
 *   Encode each digest byte as zero-padded two-digit hexadecimal text.
 *
 * Variable Index:
 * None
 *
 *
 * Constraints:
 * A client represents one connection and does not retry, buffer after failure, or accept a second connection.
 * Key State Explanation:
 * Instance's waiters store control/chunk Promises, pending stores unverified hashes and counts; sendQueue/receiveQueue
 * maintain order. sequence/sentBytes track send progress, verifiedChunks/verifiedBytes
 * increment only after verification; failure/finished/cancelled distinguish failure, success, and active cancellation.
 *
 */
/**
 * Functionality: Manage one WebSocket protocol connection and its request/response state.
 * Inputs: None; instance methods receive connection commands and payloads.
 * Outputs: Protocol requests, verified response payloads, and progress/error callbacks.
 * Logic: Serialize sends and receives, correlate response identifiers, and release pending work on completion, error, or cancellation.
 * Constraints: Instances are single-use and do not reconnect.
 */
export class StreamClient {
  /**
 *  Initialize single connection state and callbacks; timeoutMs defaults to 10 seconds but is configurable; construction does not initiate network requests.
 */
  constructor(url, { onProgress = /**
 *  If caller does not subscribe to progress, perform no additional actions; protocol counting is still maintained by the client.
 */ () => {}, onError = /**
 *  If caller does not provide error notification, perform no additional actions; failures still propagate through pending items.
 */ () => {}, timeoutMs = 10000 } = {}) {
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
 *
 * Register a unique response wait item and start timeout timer.
 * Returns: Promise that resolves when matching response arrives; clears timer and associated item on success or failure.
 * Concurrent waits with same key explicitly error out, preventing one response from satisfying multiple requests.
 *
 */
  waitFor(key) {
    if (this.waiters.has(key)) throw new Error(`Already waiting for ${key}`);
    return new Promise(/**
 *  Create a timed wait item and wrap success and failure paths to uniformly release the timer.
 */ (resolve, reject) => {
      const timer = setTimeout(/**
 *  After timeout, enter unified failure flow and reject all incomplete operations.
 */ () => this.fail(new Error(`Timed out waiting for ${key}`)), this.timeoutMs);
      this.waiters.set(key, {
        /**
 *  Clear timer, remove wait item, then fulfill corresponding Promise.
 */
        resolve: (value) => { clearTimeout(timer); this.waiters.delete(key); resolve(value); },
        /**
 *  Clear timer, remove wait item, then reject corresponding Promise.
 */
        reject: (error) => { clearTimeout(timer); this.waiters.delete(key); reject(error); },
      });
    });
  }

  /**
 *  Merge "register wait → send control message → propagate send failure" into a single path to prevent missed cleanup.
 */
  request(key, data) {
    const result = this.waitFor(key);
    try { this.sendJSON(data); } catch (error) { this.fail(error); }
    return result;
  }

  /**
 *
 * Establish a single connection and wait for hello announcement.
 * Method: Binary decode to ArrayBuffer, messages validated serially via receiveQueue.
 * Abnormal closure waits for ongoing receive tasks to finish, then checks whether normal finished confirmation is missing.
 *
 */
  async connect() {
    if (this.socket) throw new Error("Use a new client for each test.");
    const ready = this.waitFor("hello");
    this.socket = new WebSocket(this.url);
    this.socket.binaryType = "arraybuffer";
    /**
 *  Feed current message into receive queue, maintaining order of parsing and state updates.
 */
    this.socket.onmessage = (event) => {
      this.receiveQueue = this.receiveQueue.then(/**
 *  Parse current message only after prior message processing completes, avoiding concurrent validation disrupting state.
 */ () => this.receive(event.data)).catch(/**
 *  Pass exceptions from receive queue to unified failure and resource cleanup process.
 */ (error) => this.fail(error));
    };
    /**
 *  Convert transmission errors into explicit connection failure; no reconnection path established.
 */
    this.socket.onerror = () => this.fail(new Error("WebSocket connection failed. Check the ASGI server."));
    /**
 *  Wait for all received messages to be processed before determining if disconnection was premature.
 */
    this.socket.onclose = (event) => {
      /**
 *  After receive queue ends, report abnormal closure only for incomplete and uncanceled connections.
 */
      this.receiveQueue.then(() => {
        if (!this.finished && !this.cancelled) this.fail(new Error(`Connection closed before finish (code ${event.code}).`));
      });
    };
    this.hello = await ready;
    return this.hello;
  }

  /**
 *  Encode a control message; throw immediately on failure state or disconnected state, without buffering for recovery.
 */
  sendJSON(data) {
    if (this.failure) throw this.failure;
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error("WebSocket is not connected.");
    this.socket.send(JSON.stringify(data));
  }

  /**
 *  Use unique ID to associate pong, measure RTT using performance.now, without relying on server clock.
 */
  async ping() {
    const id = crypto.randomUUID();
    const started = performance.now();
    await this.request(`pong:${id}`, { type: "ping", id });
    return performance.now() - started;
  }

  /**
 *  Declare current connection mode and MIME type; Promise resolves after server confirms started.
 */
  async start(mode, mimeType) {
    return this.request("started", { type: "start", mode, mime_type: mimeType });
  }

  /**
 * Queues a Blob or ArrayBuffer for transmission and waits for its verification response.
 * Method: Sequentially convert Blob, check server capacity, compute hash, encode big-endian sequence header, then send.
 * Returns: A Promise resolving to the payload ArrayBuffer; fails explicitly on size or backpressure limits exceeded, without dropping frames.
 */
  sendChunk(payload) {
    const task = this.sendQueue.then(/**
 * Verify capacity and generate frames in send queue order, then wait for corresponding verification responses.
 */ async () => {
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

  /**
 * Wait for all fragment Promises to complete before submitting verification count, preventing premature termination and loss of trailing media.
 */
  async finish() {
    await this.sendQueue;
    if (this.failure) throw this.failure;
    return this.request("finished", {
      type: "finish", verified_chunks: this.verifiedChunks, verified_bytes: this.verifiedBytes,
    });
  }

  /**
 * Verify control messages or binary responses, then parse corresponding pending items.
 * Method: ACK must match local hash; binary data must have prior ACK and matching hash.
 * Completion count updates only based on verified payloads; unknown, out-of-order, or corrupted responses throw errors handled by fail cleanup.
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

  /**
 * Reject all pending items uniformly and clear fragment metadata; caller determines connection closure reason.
 */
  rejectPending(error) {
    for (const waiter of [...this.waiters.values()]) waiter.reject(error);
    this.pending.clear();
  }

  /**
 * Propagate first failure, close connection, and notify UI; subsequent repeated exceptions do not override original failure cause.
 */
  fail(error) {
    if (this.failure || this.finished || this.cancelled) return;
    this.failure = error;
    this.rejectPending(error);
    this.socket?.close(1000, "Test failed");
    this.onError(error);
  }

  /**
 * Proactively cancel connection and all pending operations without attempting reconnection; used for page leaving or test end cleanup.
 */
  close() {
    this.cancelled = true;
    const error = new Error("Test cancelled by client.");
    this.failure = error;
    this.rejectPending(error);
    this.socket?.close(1000, "Client closed");
  }
}

/**
 * Compute SHA-256 of input buffer and return a fixed 64-character lowercase hexadecimal string.
 */
export async function sha256(buffer) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", buffer))].map(/**
 * Encode each digest byte into a two-digit hexadecimal text representation.
 */ (b) => b.toString(16).padStart(2, "0")).join("");
}
