# 面试网页与数字人播放器

当前面试页面、语音交互与本地摄像头预览统一维护在本目录，后续由后端团队迁入根目录 `frontend/`。

- `agent.html`、`agent.js`：面试设置、简历预解析、阶段进度、回答确认与报告。
- `interview-voice.js`：问题朗读、打断、数字人状态与语音回答协调。
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
