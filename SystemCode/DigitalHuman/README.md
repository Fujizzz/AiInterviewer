# 数字人面试官

UE 5.8.3 数字人模块：MetaHuman + Pixel Streaming 2 + 动态 WAV 播放与原生音频口型。
网页、百炼 TTS/STT 接口和现有 Agent 面试协议已经接线。

已完成人物 Assembly，当前场景使用 `BP_MHC_Hannah`，并绑定控制器与摄像机。
身体保持待机动画，面部由状态、表情计划和语音口型共同控制；录制表情保留为降级方案。

详细操作见 [数字人启动与验收说明](../docs/guides/DIGITAL_HUMAN_SETUP.md)。

- 地图：`/Game/Maps/L_Interview`
- 场景控制器：`/Game/Blueprints/BP_InterviewerController`
- 角色：`/Game/MetaHumans/MHC_Hannah/BP_MHC_Hannah`
- 网页面试入口：`http://127.0.0.1:8765/agent/`
- 本机信令：网页 `8889`，UE `8888`
- 默认输出：`1920×1080`、`30 FPS`，使用 H264 编码
- 构建输出：`BuildOutput/Windows/DigitalHuman.exe`，生成文件不加入 Git
- 云渲染上传包：`BuildOutput/DigitalHuman.zip`；运行 `Tools/package.ps1` 生成，不包含调试符号、旧 ZIP 和运行日志
- 精确 cook 标签：`Content/RuntimeAssets`；保留当前原生口型模型，排除部署时未使用的视频面捕模型

完成人物组装后，在 UE 的 Python 控制台运行 `Tools/setup_scene.py`，将组装角色接入已有场景。
只有一个候选角色 Blueprint 时脚本自动选择；有多个时按说明显式指定路径。

```powershell
# 在仓库根目录运行。Python 后端需要另开终端，具体配置见操作说明。
.\DigitalHuman\Tools\setup-streaming.ps1
.\DigitalHuman\Tools\start-signalling.ps1
# 另一个终端
.\DigitalHuman\Tools\start-digital-human.ps1
```

使用本机 GPU 为已部署网页渲染时，以 `Tools/start-render-tunnel.ps1` 建立到后端服务器的
SSH 隧道，再运行 `Tools/start-digital-human.ps1`。网页通过登录认证的同源 WSS 连接，
跨网络媒体由 WebRTC 与 TURN 传输。服务器信令准备见 [部署说明](../deploy/README.md)。

已验证 Windows Development 打包、实际浏览器画面与 Data Channel 往返。
离线原生测试使用合成音调，验证动态下载、WAV 播放、求解器生成动画帧与停止行为；
它不代表已通过英文发音、真人口型同步或云端模型验收。

独立语音诊断页：`http://127.0.0.1:8765/stream-demo/digital-human-check.html`。
可测试真实 TTS、数字人播放、缓存音频重播、打断和 STT；该页不生成问题或评分。
启用 `start-digital-human.ps1 -Diagnostics` 可在本机日志中检查 Live Link 属性。
