# AI Interviewer MVP

## 功能

- 读取 TXT、文本型 PDF 简历，提取候选人信息和面试主题。
- 终端问答，支持暂停恢复、追问、换主题和题数限制。
- 分析回答，生成 1–5 分评价及 JSON 报告。
- 支持千问、OpenAI；提供离线测试。会话仅保存在内存中。

## 运行

在项目目录执行（已有 `.venv` 可跳过创建）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

在 `.env` 中配置千问；密钥已设为系统环境变量时可省略密钥行：

```dotenv
LLM_PROVIDER=dashscope
DASHSCOPE_MODEL=qwen-plus
DASHSCOPE_API_KEY=你的密钥
```

启动后按提示回答，按 `Ctrl+C` 退出：

```powershell
.\.venv\Scripts\python.exe -X utf8 main.py Wang_Shunyao_CV.pdf
```

可换成自己的 TXT/PDF 路径；默认最多 5 题，每个主题最多追问 2 次。
使用 OpenAI 时设置 `LLM_PROVIDER=openai`、`OPENAI_API_KEY`、`OPENAI_MODEL`。


