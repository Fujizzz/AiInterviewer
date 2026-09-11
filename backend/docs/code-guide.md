# 后端代码阅读指南

本目录提供练习 API、流式传输验证及 MVP Agent 文字面试。`agent_session.py` 调用根目录 `InterviewAgentService`，复用既有模型、评价及报告实现。业务库保存练习题目和作答；Agent 会话与流式诊断数据只存在于内存中。

## 阅读顺序与职责

| 顺序 | 文件或目录 | 功能及主要入口 |
| --- | --- | --- |
| 1 | `config/asgi.py`、`config/urls.py` | ASGI 生命周期、HTTP/WebSocket 分流和 URL 组合 |
| 2 | `interviews/access.py`、`middleware.py` | 共用回环地址、Host 和同源检查；HTTP 中间件实施拦截 |
| 3 | `interviews/models.py`、`docs/schema.md` | 三张业务表、状态字段、唯一性和一致性约束 |
| 4 | `interviews/api/serializers.py` | 明确输入字段、拒绝未知字段、校验动作及定义响应 |
| 5 | `interviews/api/views.py`、`services.py` | 视图适配 HTTP；业务服务执行事务和状态转换 |
| 6 | `interviews/errors.py` | 统一预期错误结构和异常上下文日志 |
| 7 | `interviews/streaming/protocol.py` | `EchoState` 校验模式、序号、容量、校验计数；生成响应 |
| 8 | `interviews/streaming/websocket.py` | 接收循环、消息调度、ACK/二进制发送和异常关闭 |
| 9 | `interviews/demo.py` | 测试页资源白名单和禁止缓存响应 |
| 10 | `frontend/stream-client.js` | 请求关联、超时、SHA-256 校验、完成确认和取消 |
| 11 | `frontend/media.js` | 媒体来源、录制、串行分片队列及资源释放 |
| 12 | `frontend/view.js`、`app.js` | DOM 与 Blob URL 管理；按钮、测试流程和页面生命周期 |
| 13 | `interviews/tests/`、`tests/` | 业务与协议边界、客户端失败语义、真实服务联调 |
| 14 | `interviews/agent_provider.py` | 显式模型配置、复用 MVP 供应商调用、脱敏日志及客户端释放 |
| 15 | `interviews/agent_session.py` | 将 MVP 用例适配为初始化、回答、报告三步，复用原有决策和计时 |
| 16 | `interviews/agent_socket.py` | `/ws/agent/` 严格命令校验、单请求执行、断线清理 |
| 17 | `frontend/agent.html`、`agent.js`、`agent.css` | 文字面试测试页，不在浏览器保存密钥或持久化结果 |

## 三条数据路径

REST 写请求经过访问检查、序列化校验后进入业务服务。更新先用版本条件 UPDATE 竞争写入资格，再检查单题状态并提交；任何验证失败都回滚版本和业务修改。SQLite 下不依赖行级锁的行为。创建场次时显式选择只查询所需题目；默认选择最多读取 101 条，第 101 条仅用于判定原有 100 题上限。

流式请求经过独立握手检查后进入连接循环。协议对象只保留计数与元数据，校验分片后先发 ACK，再原样返回二进制消息。客户端串行校验哈希和载荷，最后确认双方计数一致。服务端不保存分片；浏览器只为当前回放保留 Blob，清空、开始下一测试或离开页面时释放 URL。

Agent 连接经过同一来源策略后，将严格校验的命令交给独立内存会话。MVP 适配器执行简历解析、问题生成和评价，Agent 核心提交决策，最终由既有报告生成器计算分数。一个连接同时只处理一个命令，异常或断线时结束本地流程；在途同步 SDK 调用完成后释放客户端。

## 注释格式

每个 Python 文件以模块文档字符串说明功能、依赖关系与实现边界，使用独立的 `目录：` 和 `关键变量：` 段。目录列出本文件实际定义的类、函数和方法，使用限定名，例如 `AgentSession.answer`；嵌套函数也需列出，例如 `AgentTests.test_busy_cancel_and_disconnect_release_session.slow_start`。导入的函数和子模块放在“设计说明”中，不作为本文件实现。

“关键变量”逐项列出模块级赋值的名称与用途。类成员、实例属性和关键局部变量在“关键状态说明”中解释，重点包括状态域、计量单位、资源所有权和不变量；没有对应定义的段显式标记“无”。不必把每个循环临时变量机械列入目录。

```python
"""本文件的职责、依赖与实现边界。

目录：
- Session：
  管理单次会话。
- Session.close：
  释放该会话拥有的资源。

关键变量：
- LIMIT：
  该限制的含义与计量单位。

关键状态说明：
Session.active 表示资源仍可使用；说明变更时机与约束。
"""
```

以上只是格式示意，条目须替换为本文件真实符号。条目使用 `- 名称：说明`，或把说明放在下一条缩进行；不得保留空说明、重复条目或已删除符号。

函数和方法的 docstring 应给出职责；复杂业务方法进一步说明输入、返回值、前置条件、主要步骤、异常、事务边界和资源副作用。简单查询或测试辅助函数可用准确的一句话说明，避免无信息量的模板堆叠。测试注释说明输入场景与验证的不变量。

JavaScript 文件以 `@module` 文件头提供同样的目录与模块变量索引。目录使用限定名，例如 `StreamClient.connect`、`createDeviceSource.cleanup`，不同作用域不得共用同名条目。函数、箭头函数、类和方法前使用独立、紧邻且具有职责说明的 JSDoc；文件头不能兼作第一个声明的 JSDoc。HTML/CSS 文件头列出页面区域、关键元素或选择器与样式分组，并明确无函数定义的情况。

JavaScript 匿名函数也执行相同的双位置检查。未获得静态绑定名称的函数在所属作用域内按源码顺序命名为 `callback1`、`callback2` 等；无绑定对象使用 `object1` 等作为其属性方法的上下文。因此 `StreamClient.waitFor.callback1.object1.resolve` 表示等待器中 Promise 回调内对象的 resolve 方法。匿名编号不依赖行号，增删或移动同层回调后必须重新核对目录中的用途说明。

变量、静态属性或赋值目标绑定的函数使用绑定名称；getter/setter 分别添加 `.get` 和 `.set`，私有方法保留 `#`。函数表达式内部别名不另建一份目录。动态计算方法名或重复限定名会报错，不能以歧义目录通过检查。匿名默认导出类采用同层 `callbackN` 合成名称。

匿名函数的 JSDoc 可直接写在函数表达式前；当独立注册语句恰好只有一个直接函数参数时，也允许放在整个注册语句前，例如 `/** 验证场景。 */ test("case", () => {});`。多回调注册必须各自注释，纯标签、空 JSDoc 或被其他注释隔开的 JSDoc 均不合格。

注释采用可查证的技术说明，不引入学术引用或未经验证的复杂度、性能结论。修改行为时应同步更新契约、注释及测试，尤其不得使注释与实际失败语义脱节。

在 backend 目录安装 `python -m pip install -r requirements-docs.txt`，运行 `python tools/check_docs.py` 可检查：

- Python 所有 AST 分支中的类、函数及方法是否有 docstring；目录是否缺失或残留符号。
- 模块级赋值变量是否具有对应索引；目录条目是否重复或缺少说明。
- JavaScript 全部函数、匿名回调、类与方法是否有 JSDoc 和限定名目录；覆盖多行签名、行内对象方法、生成器、类字段及模板插值中的函数。
- JavaScript 模块变量按语法作用域提取，不依赖缩进；包括模块直属声明及顶层块中的 `var`，解构只计绑定名称。块级 `let/const`、类字段和函数局部状态另作说明。

每个声明同时满足“声明处有注释”和“顶部有非空目录条目”才算通过；目录存在不代表声明处可以省略注释。目录反向差集发现删除或重命名后的残留条目；重复段、重复条目、错误分隔符和空说明也会失败。退出码 0 表示通过，1 表示存在问题，可由 CI 调用；本次未修改仓库共享 CI 或 Git hooks。

检查器不加载业务模块或 `.env`，并在遍历前排除虚拟环境、第三方依赖和测试输出。Python 使用标准库 AST，JavaScript 使用固定版本的 Tree-sitter 及官方 JavaScript 语法库；解析器缺失、错误恢复节点或不可索引的方法都会明确失败。它不是 JavaScript 运行时或完整语言语义验证器，仍应运行 Node 语法检查及业务测试。Python lambda 不属于具名定义，HTML/CSS 注释、关键代码块、类成员与局部状态说明、注释语义，以及代码和注释是否在同一逻辑变更中交付，仍由人工核对。

修改检查器时运行 `python -m unittest discover -s tools -p "test_*.py"`，验证双位置四种组合、嵌套定义、目录过期、匿名回调、同名作用域、变量归属、JSDoc 缺失、语法错误与缺失依赖等边界。不要通过排除业务文件或降低校验规则来隐藏缺失项。

实现依据：[Tree-sitter Python 官方接口](https://github.com/tree-sitter/py-tree-sitter)、[JavaScript 官方语法库](https://github.com/tree-sitter/tree-sitter-javascript)。调研中也核对了 [eslint-plugin-jsdoc 的 require-jsdoc 规则](https://github.com/gajus/eslint-plugin-jsdoc/blob/main/docs/rules/require-jsdoc.md)：可用于声明处 JSDoc 要求；本项目额外需要中文文件目录的双向关联，因此沿用 Python 检查入口并使用语法树扩展，避免再建立一套 Node 开发依赖。

## 清理与兼容边界

原平铺的 REST 文件已迁入 `api/`，旧流式大文件已替换为 `streaming/` 包；调用点均使用新路径，没有保留重复入口。访问判断、客户端控制请求和待处理请求清理使用共用实现。

迁移 `0001`、`0002`、`0003` 保留完整历史。`0001` 中的旧诊断表由 `0003` 删除，当前模型中不存在该表；保留历史是为了让已有开发数据库正常升级，不应直接删除或改写已应用迁移。

原有练习和媒体计时、容量、编码、超时与失败规则不变。Agent 使用独立连接并沿用 MVP 的每题 120 秒预算、模型重试和备用逻辑，不与练习计时合并。Agent 配置和协议见 `agent-integration.md`。
