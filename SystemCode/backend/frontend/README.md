# 面试网页与数字人播放器

当前面试页面、语音交互与本地摄像头预览统一维护在本目录，后续由后端团队迁入根目录 `frontend/`。

- `agent.html`、`agent.js`：面试设置、简历预解析、阶段进度、自动答题与报告、提前结束保存选择。
- `interview-voice.js`：问题朗读、打断、数字人状态、10 秒准备／5 秒静默倒计时与完整回答自动提交。
- `speech-capture.js`、`speech-worklet.js`、`pcm-resampler.js`：麦克风采集、16 kHz PCM 和转录草稿。
- `digital-human/`：UE 5.8 官方 Pixel Streaming SDK 包装与构建。

在仓库根目录执行：

```powershell
pnpm --dir backend/frontend/digital-human install --frozen-lockfile
pnpm --dir backend/frontend/digital-human build
pnpm --dir backend/frontend/digital-human test
node --test backend/tests/*.test.mjs
```

生成的 `dist/` 和依赖目录不提交；后端通过白名单提供播放器文件。
本机 UE 与后端启动方式见 [数字人操作说明](../../docs/guides/DIGITAL_HUMAN_SETUP.md)。

每题语音合成、UE 口型准备及朗读完成后，面试主界面才显示完整的 10 秒准备倒计时，结束后自动采集。关闭朗读、主动打断或朗读失败时，直接开始准备；重新朗读会暂停准备计时，播放结束后继续剩余时间。网页分别显示“生成语音”和“准备口型”，准备倒计时不会取消正在生成或播放的问题。语音活动刷新 5 秒静默收尾时间。开始／结束回答按钮已移除。结束面试弹窗提供评判并保存、结束且不保存与继续选项。失败转写不替换为草稿；空白最终转写只记为未作答。

UE 口型准备会根据后端提供的 WAV 时长计算等待上限（18 至 125 秒）；时长缺失或异常时沿用 18 秒。此上限只用于故障降级，朗读完成后的 10 秒准备时间单独计算。打开结束面试选项会暂停准备计时，选择继续后恢复剩余时间。
