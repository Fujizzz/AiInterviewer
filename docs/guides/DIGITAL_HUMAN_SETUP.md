# 数字人面试官：启动、角色接线与验收

## 目前已实现什么

| 部分 | 当前状态 |
| --- | --- |
| 固定面试场景 | 已保存 `L_Interview`：背景、地面、主光/补光、胸部以上机位；已设为默认地图 |
| 控制器 | 已生成 `BP_InterviewerController`，管理 Idle / Listening / Thinking / Speaking / Interrupted |
| 原生音频桥接 | 已编译、打包并运行：HTTP 下载 WAV，同一份 PCM 分别用于播放和 MetaHuman 音频求解 |
| 网页数字人播放器 | 使用 Epic UE 5.8 官方 SDK；实际浏览器已显示打包场景并收到 UE 控制器反馈 |
| TTS | 已接入 Qwen3-TTS-Flash-Realtime，支持显式地区配置，当前生产使用北京；收集实时 PCM 后封装整句 WAV，临时音频缓存有容量和期限限制 |
| STT | 已接入 Qwen-Audio-3.1-ASR-Flash-Streaming：浏览器 PCM → 语音 WebSocket → 中间/最终转录 → 可修改的回答框 |
| 面试闭环 | 接入原有 question / answer / finished 消息；支持 15 秒准备、5 秒静默自动提交及语义结束确认后的 MCP 提交，均交给原 Agent 评价 |
| 人物 Rig 与 Assembly | **已完成**；当前场景已放入 `BP_NewMetaHumanCharacter`，Avatar 与 InterviewCamera 引用已核对 |
| 自然身体动作 | 状态事件已提供；**尚未配置身体待机循环和手势动画资产** |
| 云端英文语音 | **短句实测通过**：24 kHz PCM16 TTS 和 16 kHz PCM STT；尚未完成真实麦克风与技术术语的整轮验收 |
| 真人完整验收 | **尚未完成**：真实人物口型、身体动作、五轮面试及十分钟稳定性仍需现场确认 |

初次打包验证时人物尚未组装，程序显示背景。最新组装角色与场景已重新打包，浏览器可显示固定机位的人物，串流约 27–30 FPS。离线原生测试使用合成音调；云端短句测试单独记录。

```mermaid
flowchart LR
    Agent[现有面试 Agent] -->|问题文本| Page[网页交互与字幕]
    Page -->|POST TTS| Speech[后端百炼语音适配]
    Speech -->|临时 WAV URL| Page
    Page -->|Data Channel: speak| UE[UE 面试控制器]
    UE -->|下载同一份 PCM| Audio[声音播放 + 原生面部求解]
    Audio --> Avatar[组装后的 MetaHuman]
    Avatar -->|Pixel Streaming 画面与声音| Page
    Mic[浏览器麦克风] -->|16 kHz PCM WebSocket| Speech
    Speech -->|转录草稿与最终文本| Page
    Page -->|静默自动提交或语义确认后的 MCP 提交| Agent
```

数字人只负责呈现；简历分析、追问和评分由既有 Agent 负责。用户的麦克风不连接面试官口型，也不经 Pixel Streaming 上行。

## 人物资产与当前场景

本机当前人物已完成组装。地图为 `/Game/Maps/L_Interview`，控制器为 `/Game/Blueprints/BP_InterviewerController`，角色为 `/Game/MetaHumans/NewMetaHumanCharacter/BP_NewMetaHumanCharacter`。
已检查当前场景的 Avatar 与 InterviewCamera 绑定；不要为重复测试重建现有布景。以下组装步骤供新机器或新人物使用。

1. 在 UE 打开 `Content/MetaHuman/NewMetaHumanCharacter`，确认人物脸型、发型和服装。
2. 如编辑器要求登录 Epic 或确认协议，请由账户所有者完成对应界面。
3. 在 Rig 区域完成角色绑定，再执行 **Download Texture Sources**。
4. 在 Assembly 选择 **UE Optimized → Medium**，执行 **Assemble**。
5. 找到生成的角色 Blueprint。后续需要的是这个 Blueprint，不能把原始 Character 编辑资产赋给控制器。

Epic 的组装/绑定入口见 [MetaHuman Creator 入门](https://dev.epicgames.com/documentation/en-us/metahuman/getting-started-with-metahuman-creator)、[Live Link 角色设置](https://dev.epicgames.com/documentation/en-us/metahuman/realtime-animation-using-live-link)。

组装完成后，打开 UE 的 Output Log，将命令输入模式切换为 Python，运行：

```python
exec(open(r"D:\_Project\AiInterviewer\DigitalHuman\Tools\setup_scene.py", encoding="utf-8").read())
```

脚本复用已经创建的地图和控制器，不重建现有布景。只有一个 MetaHuman 文件夹下的 `BP_` 角色时会自动选择。
如果有多个角色，或角色保存在其他文件夹，先指定真实资产路径：

```python
import os
os.environ["INTERVIEWER_AVATAR_ASSET"] = "/Game/MetaHumans/YourCharacter/BP_YourCharacter"
exec(open(r"D:\_Project\AiInterviewer\DigitalHuman\Tools\setup_scene.py", encoding="utf-8").read())
```

把示例路径替换成内容浏览器中角色 Blueprint 的实际路径。也可以直接打开 `L_Interview`，放入角色，将它赋给 `InterviewerController` 的 **Avatar**。
已有 Avatar 不会被脚本替换；更换人物时在控制器的 Details 面板手动重新指定。

运行结果保存在 `DigitalHuman/Saved/InterviewSetup.json`。成功接线时 `avatar_bound` 应为 `true`。
按 Play 检查机位和材质，并为身体配置一个合适的站立待机循环。身体动画要与面部 Live Link 分开，不能用身体动画覆盖面部动画图。

控制器会在 Speaking 时设置 `InterviewerAudio` 和 Use Live Link；如组装版本使用其他变量名称，按 Epic 文档在角色的 Live Link 区域手动核对。
`BP_InterviewerController` 的 `OnStateChanged` 可供蓝图扩展身体动作；当前没有点头或手势素材。

## 配置后端语音

在 **项目根目录 .env** 增加以下设置；终端 MVP 与后端共用该文件，已有配置文件不要覆盖：

```dotenv
SPEECH_ENABLED=true
SPEECH_REGION=singapore
DASHSCOPE_API_KEY=<与 SPEECH_REGION 对应地域的 key>
DASHSCOPE_SPEECH_WORKSPACE_ID=
SPEECH_TTS_MODEL=qwen3-tts-flash-realtime
SPEECH_TTS_VOICE=Cherry
SPEECH_STT_MODEL=qwen-audio-3.1-asr-flash-streaming
DJANGO_SECRET_KEY=<独立生成的 Django secret>
```

语音服务默认 `SPEECH_ENABLED=false`、`SPEECH_REGION=singapore`；未设置地域的协作者保持原有新加坡行为。
如复用北京地域的现有 Key，仅在自己的根目录 `.env` 设置 `SPEECH_REGION=beijing`，不替换 Key、不改文字模型配置。
仅支持 `singapore` 和 `beijing`，空值或其他地域明确报配置错误，不从文字模型地址推断地域、不自动跨地域重试。
启用前，在百炼控制台确认两个模型的权限、免费额度有效期和余量；对支持的模型启用 **Free Quota Only**。
代码不能替你开通控制台的免费额度限制；额度错误会显示 `quota_exhausted` 并保留文字操作。
不能从现有 LLM 的额度推断语音模型也有免费额度。

语音合成和识别同时按地域选择各自的官方地址：

```text
# SPEECH_REGION=singapore（默认）：STT / TTS
wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference
wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime
# SPEECH_REGION=beijing：STT / TTS
wss://dashscope.aliyuncs.com/api-ws/v1/inference
wss://dashscope.aliyuncs.com/api-ws/v1/realtime
```

它们独立于 LLM 的 OpenAI-compatible `DASHSCOPE_BASE_URL`。Key 必须与语音地域一致，不能跨地域混用。
`DASHSCOPE_SPEECH_WORKSPACE_ID` 可留空；如显式配置，STT 使用该地域的工作空间域名：
新加坡为 `{workspace}.ap-southeast-1.maas.aliyuncs.com`，北京为 `{workspace}.cn-beijing.maas.aliyuncs.com`。
TTS 保持所选地域的公开 realtime 地址。设置地域不改变模型、音色、音频格式和超时默认值。
地址契约见 [TTS SDK](https://www.alibabacloud.com/help/zh/model-studio/qwen-tts-realtime-python-sdk) 和
[ASR WebSocket](https://help.aliyun.com/zh/model-studio/qwen-audio-asr-streaming-websocket-api)。

原始数字人集成验收使用新加坡凭据、`qwen3.7-plus` 文字模型及 `qwen-audio-3.1-asr-flash-streaming`。
该记录不代表其他机器的本地配置或模型额度；每位协作者维护自己的被 Git 忽略的 `.env`。
TTS 的 `qwen3-tts-flash-realtime` 与 ASR 单独计费和核算额度，ASR 额度不能用于文字转语音。
Qwen TTS 使用新的专用 SDK 适配；把 CosyVoice 的 model 字符串换成 Qwen 名称不足以兼容协议。
当前适配支持 Qwen3-TTS-Flash-Realtime 系列与 Qwen-Audio-3.0/3.1-ASR-Flash-Streaming；HTTP TTS、Voice Design、声音注册和 Fun-ASR 文件识别不能直接填入这些实时模型配置。
默认音色 `Cherry` 支持英文；可按 [官方音色列表](https://help.aliyun.com/en/model-studio/qwen-tts-voice-list) 选择匹配的预设音色。
参考 [Qwen realtime TTS SDK](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-realtime-python-sdk)、[Qwen streaming ASR SDK](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-audio-asr-streaming-python-sdk)、[免费额度说明](https://www.alibabacloud.com/help/en/model-studio/new-free-quota)。

在仓库根目录启动后端：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe backend\manage.py migrate
.\.venv\Scripts\python.exe -m uvicorn config.asgi:application --app-dir backend --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

已有 `.venv` 可直接复用；新机器先创建 Python 3.11+ 虚拟环境。模型问题由现有 Agent 生成，其配置见 [Agent 接入说明](../../backend/docs/agent-integration.md)。
`manage.py runserver` 不能提供这里的语音 WebSocket。

## 启动 UE 与网页

需要 Node.js 22+、pnpm、Git 和本机 UE/Visual Studio C++ 工具链。首次构建串流依赖，在仓库根目录执行：

```powershell
.\DigitalHuman\Tools\setup-streaming.ps1
```

脚本安装 UE 5.8 官方 Infrastructure 的 Common/Signalling 库，并构建网页 SDK 包。
网页播放器源码、构建配置和测试统一维护在 `backend/frontend/digital-human/`；后续由后端团队迁入根目录 `frontend/`。
只重建播放器时，在仓库根目录执行 `pnpm --dir backend/frontend/digital-human run build`，
语音前端测试执行 `pnpm --dir backend/frontend/digital-human test`。
本次使用的官方源码版本为 `6b8cfb460bda09703e85178f1f77aa6faec9e890`。
下载目录、node_modules、打包输出和 UE 缓存均被 Git 忽略。

在两个额外终端分别运行：

```powershell
# 终端 2：本机信令。仅监听回环地址，不需要公网 STUN/TURN。
.\DigitalHuman\Tools\start-signalling.ps1
```

```powershell
# 终端 3：已打包的 UE 程序，720p，帧率上限 30。
.\DigitalHuman\Tools\start-digital-human.ps1
```

打开 `http://127.0.0.1:8765/agent/`，点击 **连接／播放数字人**。
信令地址保留 `ws://127.0.0.1:8889`。浏览器首次播放需要点击；正式业务页面迁移时也要保留用户触发播放的入口。
使用一个浏览器会话。当前基础设施最多允许一个播放器订阅同一 UE 进程。

开发期间也可用 `start-digital-human.ps1 -EditorGame`。最终验收应关闭编辑器并使用打包版本。
当前 UE 5.8 实际支持的参数是 **`-PixelStreamingConnectionURL`**，不是 `-PixelStreaming2ConnectionURL`。

新增或修改人物/动画资产后必须重新打包：

```powershell
.\DigitalHuman\Tools\package.ps1
```

输出为 `DigitalHuman/BuildOutput/Windows`。脚本默认引擎位置 `D:\Epic Game\UE_5.8`，其他机器可传入 `-EngineRoot`。
语音求解模型 `/StreamingADA` 已加入 Always Cook；保留该配置，否则编辑器能运行而打包版本可能缺少模型。

## 模拟面试操作

1. 点击数字人连接/播放按钮，确认角色可见。
2. 填写英文简历和目标岗位，开始面试。
3. 后端返回完整问题，网页显示字幕并请求 TTS。
4. 题目显示后准备 15 秒，倒计时结束自动开启麦克风；首次需授权浏览器麦克风，可用中文或英文回答。
5. 连续静默 5 秒或检测到结束意图时自动收尾，等待完整最终转写。回答最多 120 秒，到限也自动收尾。
6. 字幕用于查看转写，完整回答自动提交；页面不提供回答编辑或手动开始／提交按钮。可随时通过 **结束面试** 选择评判并保存、结束且不保存或继续。
7. 下一题重复上述过程，最终显示原有 Agent 生成的报告。

临时转录不触发追问。正常情况下声音只由 UE 播放；数字人不可用时才由浏览器播放语音。
拒绝麦克风权限、语音模型失败或串流断线时可以继续文字回答。
点击 **打断朗读** 会取消本地下载/播放和面部 Source，并恢复回答操作；已发出的云端 TTS 请求不保证在供应商处立即取消。

## 语音接口

| 接口 | 输入 / 输出 |
| --- | --- |
| `POST /api/speech/tts/` | `{"text":"..."}`，最多 1200 字符；返回 `utterance_id`、`audio_url`、`sample_rate`、`generation_ms` |
| `GET /api/speech/audio/<uuid>/` | 24 kHz、mono、PCM16 WAV；缓存最多 16 段/32 MiB，10 分钟过期 |
| `/ws/speech/stt/` | hello → `{"type":"start"}` → PCM 二进制 → `{"type":"stop"}` → final |

STT PCM 为 16 kHz、mono、PCM16 little-endian，每片不超过 8192 字节。
前端 AudioWorklet 直接生成 PCM，不发送 MediaRecorder 的 WebM/Opus 分片。
中间事件为 `partial`；最终事件为 `final`，含 `text` 和 `finalization_ms`。
后端 15 秒内未得到最终转录会失败；SDK 网络任务另有 140 秒总超时。

UE 控制消息：

```json
{
  "type": "speak",
  "utterance_id": "<TTS 返回的 UUID>",
  "audio_url": "http://127.0.0.1:8765/api/speech/audio/<相同 UUID>/"
}
```

其余消息为 `{"type":"stop"}`、`{"type":"state","state":"listening"}`。
UE 返回 `playback_started` / `playback_finished` / `interrupted` / `playback_failed`，携带同一个 `utterance_id`。
网页以问题生命周期和语音 ID 隔离旧响应，旧音频完成事件不能释放新问题的控件。
UE 下载地址固定为本机 `8765` 的语音路由；更换后端端口需要同步调整 C++ 校验并重新编译。

## 验证与剩余验收

本轮已通过：Python 核心测试、Django/ASGI 测试、前端 PCM/生命周期测试、原有真实网络离线联调、注释契约检查、Windows 打包和实际浏览器 Data Channel 往返。
实际打包程序运行 `Interviewer.NativeAudioSmoke`，检查动态下载、声音播放、原生动画帧生成、第二段音频和主动打断。
2026-10-01 的打包验证通过：第一段生成 126 帧原生动画数据，第二段播放后成功打断，音频停止且 Live Link Subject 被移除。
Qwen 适配迁移后重新运行后端测试；前端 PCM/生命周期的 6 项测试此前通过，接口保持兼容。依赖一致性与注释契约检查通过。

2026-10-01 另执行了一次真实新加坡语音调用：输入 58 字符的公开测试句，TTS 约 1375 ms 返回 3.28 秒、24 kHz 单声道 PCM16 WAV。
将该音频转换成 16 kHz PCM 并实时发送给 STT，最终识别为 `Could you describe your main contribution to this project?`。
测试没有使用简历或用户麦克风；它证明短句云端调用可用，不证明实际面试中的识别精度。
测试音频与识别结果位于 `DigitalHuman/Saved/SpeechValidation`，该目录不进入 Git。

离线音频测试需要停止真实后端，另开终端运行 `DigitalHuman/Tools/audio_smoke_fixture.py`，然后对 Windows Development 包运行该 Automation 测试。
在仓库根目录运行：

```powershell
# 终端 1：专用离线音频端点，不能与真实后端同时占用 8765。
.\.venv\Scripts\python.exe DigitalHuman\Tools\audio_smoke_fixture.py
```

```powershell
# 终端 2：测试完成后 UE 自动退出，退出码应为 0。
$audioTestArgs = @(
    '-RenderOffscreen', '-AudioMixer', '-ResX=1280', '-ResY=720', '-Windowed',
    '-ExecCmds="t.MaxFPS 30,Automation RunTests Interviewer.NativeAudioSmoke"',
    '-TestExit="Automation Test Queue Empty"',
    '-ReportExportPath=D:/_Project/AiInterviewer/DigitalHuman/Saved/NativeAudioTest'
)
$audioTestProcess = Start-Process -FilePath '.\DigitalHuman\BuildOutput\Windows\DigitalHuman\Binaries\Win64\DigitalHuman.exe' -ArgumentList $audioTestArgs -WindowStyle Hidden -Wait -PassThru
$audioTestProcess.ExitCode
```

本项测试不需要浏览器或信令服务；结束后在终端 1 按 Ctrl+C，释放真实后端的端口。
测试使用明确标记的合成音调；不能据此宣称英文 TTS 或真人口型已验收。
报告位于 `DigitalHuman/Saved/NativeAudioTest/index.json`，启动/串流日志位于 `DigitalHuman/Saved/Logs`。

人物和云服务配置完成后，执行以下最终验收：

- 两个刚合成、未导入 Content Browser 的英文问题均有声音和对应口型。
- 连续五轮：问题、字幕、声音、转录和 question_id 对应；修改后的确认文本进入评价。
- 打断后声音和嘴部动画均停止，下一题不会继续旧动画。
- 验证麦克风拒绝、TTS/STT 失败、音频过期及 Pixel Streaming 断线的文字操作。
- 关闭编辑器，以打包程序连续运行十分钟；核对人物材质、身体姿态和动作。

网页分别显示问题等待、TTS 生成/请求、UE 播放准备、STT 收尾时间及接收帧率。
“问题等待”包括后端业务处理和模型调用，不等同于纯模型推理时间。
嘴部与声音的偏差需观察真实角色后测量；`Speech.AudioDelaySeconds` 初值 0.16 秒，可在蓝图 Details 中调整。
这些时延、角色帧率和十分钟稳定性不能从空场景或合成音调测试推断。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `DigitalHuman/Plugins/InterviewerRuntime` | 原生 WAV 播放、MetaHuman 音频 Source、状态和 Pixel Streaming 消息 |
| `DigitalHuman/Tools/setup_scene.py` | 生成/复用地图、控制器、摄像机，绑定已组装人物 |
| `DigitalHuman/Tools/*streaming*.ps1` / `local-signalling.cjs` | 官方依赖构建和仅回环的信令服务 |
| `DigitalHuman/Tools/start-digital-human.ps1` / `package.ps1` | UE 运行参数与打包 |
| `backend/interviews/speech` | 密钥、地区、TTS、STT、临时 WAV 缓存及访问策略 |
| `backend/frontend/interview-voice.js` | 页面语音状态、异常降级及旧事件隔离 |
| `backend/frontend/speech-capture.js` / `speech-worklet.js` / `pcm-resampler.js` | 采集、重采样、最终转录边界 |
| `backend/frontend/digital-human` | 官方 Pixel Streaming SDK 包装、构建和前端测试 |

数字人呈现仍需本机 GPU 运行；尚未加入流式 TTS、自动语音打断、声音克隆或招聘者端。
网页自动结束回答见[结束检测说明](../../backend/docs/answer-completion.md)，公网后端配置见
[部署说明](../../deploy/README.md)。两者不代表真实数字人设备体验已经验收。
