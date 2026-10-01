# REST 与 WebSocket 接口

基址：`http://127.0.0.1:8765`。路径保留末尾 `/`，请求体为 `application/json`。
仅供本机同源使用，没有账号鉴权。未知或只读输入字段返回 400。

## REST 一览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health/` | 数据库连通性检查 |
| GET / POST | `/api/questions/` | 分页题库 / 新增题目 |
| GET / PATCH | `/api/questions/{id}/` | 查询 / 修改，可设置 enabled=false |
| GET / POST | `/api/sessions/` | 分页历史 / 创建场次 |
| GET | `/api/sessions/{id}/` | 场次与单题快照详情 |
| PATCH | `/api/sessions/{id}/items/{item_id}/` | 单题 start / complete / skip |
| POST | `/api/sessions/{id}/finish/` | 结束场次，未完成题目记 skipped |
| POST | `/api/recommendations/jobs/` | 实验性岗位排序，允许资料缺失 |
| POST | `/api/recommendations/candidates/` | 实验性候选人排序，允许资料缺失 |

推荐接口使用独立的严格JSON契约，返回未校准排序分数和缺失特征信息；
完整调用示例与模型限制见[缺失资料推荐说明](recommendation.md)。

列表格式为 `{"count":2,"next":null,"previous":null,"results":[...]}`，每页 50 条，用 `?page=2` 翻页。

### 新增题目

```http
POST /api/questions/
Content-Type: application/json

{"text":"Describe a challenge you faced.","position":3,"enabled":true}
```

返回 201，包含 id、text、position、enabled、created_at、updated_at。

### 创建场次

POST `/api/sessions/`，空对象 `{}` 使用全部启用题目，也可传 `{"question_ids":["<UUID>","<UUID>"]}` 指定顺序。
返回 201，结构如下（尖括号代表示意值）：

```json
{
  "id": "<session UUID>",
  "status": "active",
  "version": 1,
  "prep_seconds": 10,
  "answer_seconds": 90,
  "started_at": "<ISO 8601 UTC>",
  "finished_at": null,
  "items": [{
    "id": "<item UUID>",
    "question_id": "<question UUID>",
    "question_text": "What are your career goals for the next three years?",
    "position": 1,
    "status": "pending",
    "answer_text": "",
    "duration_ms": null,
    "started_at": null,
    "finished_at": null
  }]
}
```

### 单题动作与结束场次

向单题路径 PATCH：

```json
{"version":1,"action":"start"}
```

返回 200 和完整场次，version 更新为 2。然后可提交：

```json
{"version":2,"action":"complete","answer_text":"My answer...","duration_ms":90000}
```

answer_text、duration_ms 仅 complete 可携带，均可省略；duration_ms 范围 0–2147483647。
skip 示例：`{"version":3,"action":"skip"}`。向 finish 路径 POST `{"version":3}` 可结束整个场次。
每次动作使用上一响应的 version，不能固定写死示例版本。重复请求因旧版本返回 409，不会重复修改数据。

### 错误响应

```json
{"error":{"code":"conflict","detail":{"detail":"Session is completed or version is stale. Fetch its current state before deciding the next action."}}}
```

detail 可为字符串、字段对象或错误列表；按 HTTP 状态和 code 分支，不解析英文文本。

| 状态 | 含义 |
| --- | --- |
| 400 | 参数、JSON、题库选择错误 |
| 403 | 非回环来源或跨源访问 |
| 404 | 资源不存在，或题目不属于该场次 |
| 409 | 版本冲突、非法状态转换、场次已结束 |
| 415 | 不支持的 Content-Type |
| 500 / 503 | 内部错误 / 数据库不可用，详情写服务端日志 |

409 后先 GET 最新状态，由调用方决定下一步，不自动重试写请求。

## WebSocket：`/ws/echo/`

连接地址：`ws://127.0.0.1:8765/ws/echo/`。每条连接对应一次测试，接受后服务端发送：

```json
{"type":"hello","connection_id":"<temporary UUID>","max_chunk_bytes":2097152,"max_total_bytes":33554432,"idle_timeout_seconds":30}
```

单分片净载荷 2 MiB、总净载荷 32 MiB，30 秒没有收到应用消息则终止。控制 JSON 最多 4096 bytes。
这些是传输测试限制，与面试 10/90 秒配置无关。

### 1. Ping / Pong

发送 `{"type":"ping","id":"test-001"}`，回应：

```json
{"type":"pong","id":"test-001","server_time_ms":0}
```

实际 server_time_ms 为当前 Unix 毫秒时间，id 为 1–64 字符串。RTT 使用客户端单调时钟计算。
此应用消息不同于 WebSocket 协议自带的 ping 控制帧。

### 2. 声明测试类型

```json
{"type":"start","mode":"synthetic","mime_type":"video/webm;codecs=vp8,opus"}
```

mode 为 binary / audio / video / synthetic，mime_type 为 1–100 字符。
回应 `{"type":"started","mode":"synthetic"}`；每条连接只允许 start 一次。

### 3. 连续二进制分片

```text
+--------------------------+------------------------------+
| uint32 序号，大端，4 字节 | 非空净载荷，1 .. 2 MiB       |
+--------------------------+------------------------------+
```

序号从 1 开始连续递增。每个有效分片先收到 ACK：

```json
{"type":"ack","sequence":1,"bytes":32768,"sha256":"<64 hexadecimal characters>"}
```

随后服务端原样回传整个二进制消息，含序号头。前端检查原载荷、ACK 哈希和回传载荷一致性。
前端设置 binaryType=arraybuffer，用队列保持 Blob 转换顺序，等待每个分片验证后继续发送。
待发送媒体超过 4 MiB 即显式失败，不丢帧或偷偷降低码率。

每个 MediaRecorder Blob 不保证可单独播放。前端按序收集回传净载荷，停止后等末尾分片校验完成，再用相同 MIME 类型组成完整 Blob。

### 4. 完成

```json
{"type":"finish","verified_chunks":12,"verified_bytes":393216}
```

计数须为整数且与服务端一致。服务端直接发送 finished 对象：包含 connection_id、status、mode、mime_type、chunk_count、byte_count、verified_chunks、error_code，然后以 1000 关闭。
统计只在当前连接内存中维护，不落库、不写文件，也不提供历史查询。connection_id 仅用于标识当前连接。
纯 ping 可以不发送 start，直接用两个 0 完成。

### 异常与关闭

```json
{"type":"error","code":"sequence_mismatch","detail":"Sequences must start at 1 and increase by exactly 1."}
```

无 start、乱序、JSON 错误、未知命令、校验数不匹配等以 1008 关闭；大小限制 1009；内部错误 1011。
来源检查失败会拒绝握手升级。前端超时或异常关闭会显示失败并停止采集，不自动重连。

## 前端客户端复用

```javascript
import { StreamClient } from "/stream-demo/stream-client.js";
const client = new StreamClient("ws://127.0.0.1:8765/ws/echo/", {
  onProgress: ({ chunks, bytes, rttMs }) => console.log({ chunks, bytes, rttMs }),
});
try {
  await client.connect();
  await client.ping();
  await client.start("binary", "application/octet-stream");
  const echo = await client.sendChunk(new Uint8Array([1, 2, 3]).buffer);
  await client.finish();
} finally {
  client.close();
}
```

sendChunk 支持 Blob / ArrayBuffer，Promise 在对应回传通过校验后完成。close 明确取消未完成操作。

## MVP Agent：`/ws/agent/`

已提供独立文字面试接口及 `/agent/` 浏览器测试页，支持 `start`、`answer`、`cancel`。
每个连接一场面试，复用 MVP 出题、评价和报告；写入独立 Agent 表，不与固定题库练习场次混用。
模型配置、完整消息结构、重复请求规则和取消限制见 [Agent 接入说明](agent-integration.md)。

## Agent 历史（只读、本机访问）

| 方法与路径 | 返回内容 |
| --- | --- |
| `GET /api/agent-interviews/` | 按创建时间倒序分页列出 ID、岗位、生命周期、版本和时间；不含资料及回答正文 |
| `GET /api/agent-interviews/{id}/` | 候选人/岗位资料、当前状态、题目及已接受回答、已完成报告；`can_resume` 当前为 false |
| `GET /api/agent-interviews/{id}/requests/` | 分页请求元数据：UUID、类型、状态、固定错误码与时间 |
| `GET /api/agent-interviews/{id}/requests/{request_id}/` | 所属面试中单条请求的状态及保存响应；不触发调用或重试 |

分页沿用每页 50 条；状态/时间使用服务器数据。所有历史响应设置 `Cache-Control: no-store, private`。
不存在或不属于该场面试的请求返回 404；不开放新增、修改、删除或恢复操作。
详情回答的 `evaluation`、`committed_state_version` 同时为空表示回答已经接收、尚未提交评分。
Agent 已结束但报告生成失败时，`final_report` 仍为空，不把部分状态伪装成完整报告。
成功响应只表示服务器保存完成，不保证浏览器收到；查询已保存结果不会再次计费。
数据库记录包含敏感面试内容；当前依靠本机访问边界，UUID 不代替用户权限。
