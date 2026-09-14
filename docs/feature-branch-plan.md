# Feature Branch 规划与当前 Demo 状态

## 0. 当前 Demo 实现状态

更新时间：2026-09-13

当前分支：`feature/rag-chat-integration`

当前 demo 已完成一个本地可跑的中文学习聊天闭环：

```text
网页聊天
→ FastAPI /api/chat
→ 保存 user message
→ analyze problem_type / language_point
→ 生成 query embedding
→ 检索 teaching_index top 3
→ 检索 level_index top 3
→ 拼接 kernel + workflow + runtime context
→ 调用 GCP GenAI stream_reply
→ 后端转换为 SSE
→ 前端流式渲染
→ 保存 assistant message、agent_events、完整 trace
```

已完成：

- `feature/bootstrap-fastapi-chat`：FastAPI、静态聊天页、健康检查、SSE 聊天接口。
- `feature/sqlite-chat-logs`：SQLite conversations/messages/agent_events，每日 JSONL，完整 trace JSON。
- `feature/rag-embedding-index`：`teaching_index` / `level_index` 双路 index，GCP embedding，source hash 复用，batch 成功后即时落库。
- `feature/gcp-model-client`：`embed_texts` 和 `stream_reply` 统一封装 GCP GenAI 调用。
- `feature/rag-chat-integration`：query embedding、双路 top 3 检索、runtime context、GCP stream 到 SSE。
- `feature/prompt-workflows`：kernel + workflow prompt 拆分，并调整为默认自然段回答，避免练习册题解口吻。
- 基础前端渲染：支持流式状态、token 渲染、粗体 Markdown 的安全展示，避免用户看到裸 `**`。

当前知识库和索引状态：

- 教材索引：`知识库min/教材/发展汉语高级阅读1.md`、`知识库min/教材/发展汉语高级阅读1参考答案.md`。
- 水平边界索引：`HSK4词汇.md`、`HSK5词汇.md`、`HSK语法1-5.md`。
- 当前实现中，HSK4 词汇每 15 个词一组，HSK5 词汇每 8 个词一组，语法按单个语法项目切分。
- 最近一次本地索引检查约为 `teaching_index=143`、`level_index=155`，以 `scripts/verify_rag_index.py` 实际输出为准。

当前 debug 入口：

- SQLite：`data/app.sqlite3`
- 每日 JSONL：`chat_logs/YYYY-MM-DD.jsonl`
- 完整 trace：`chat_logs/traces/YYYY-MM-DD/{conversation_id}_{timestamp}.json`
- trace 中记录 analyze、prompt、RAG chunks、model metadata、assistant reply 和前端 event payload。

仍然属于 demo 限制的部分：

- 检索使用 SQLite 全量扫描余弦相似度，没有引入生产向量库。
- analyze 仍是轻量规则，不是独立 LLM 分类器。
- 自动化端到端验证依赖本地 GCP key、网络和已构建索引。
- 当前 focus 是知识性问答和语言点引导，不做练习册答题产品。

## 1. Feature Branch 规划

建议每个 feature branch 只完成一个可验证切片，避免把 RAG、前端、prompt、数据系统混在一个大分支里。

### 1.1 `feature/bootstrap-fastapi-chat`

目标：跑通最小聊天壳。

范围：

- FastAPI 项目骨架。
- 静态聊天页面。
- `/api/health`。
- `/api/chat` mock streaming。

验收：

- 本地能打开页面。
- 输入消息后能看到流式 mock 回复。

### 1.2 `feature/sqlite-chat-logs`

目标：保存基础会话数据。

范围：

- SQLite 初始化。
- `conversations`、`messages`、`agent_events` 表。
- 每日 JSONL 追加。

验收：

- 用户消息和 Agent 回复都能落库。
- `chat_logs/YYYY-MM-DD.jsonl` 有记录。

### 1.3 `feature/rag-embedding-index`

目标：完成双路 embedding index。

范围：

- 读取 `知识库min`。
- 构建 `teaching_index` 和 `level_index`。
- 通过 GCP GenAI embedding model 生成并缓存 embedding。
- 源文件 hash 变化时可重建。
- 默认使用 `gemini-embedding-2`，通过环境变量读取 `GCP_EMBEDDING_MODEL`。

验收：

- 首次运行生成 embedding。
- 第二次运行复用 embedding。
- 能分别查询 teaching 和 level top k。

### 1.4 `feature/gcp-model-client`

目标：封装 GCP GenAI LLM 和 embedding 调用。

范围：

- 新增 `app/model_client.py`。
- 实现 `embed_texts(texts)`。
- 实现 `stream_reply(input_text)`。
- embedding 使用：
  - `from google import genai`
  - `client.models.embed_content(model="gemini-embedding-2", contents=...)`
- LLM 使用：
  - `client.interactions.create(model="gemini-3.8-flash", input=..., stream=True)`
- 读取环境变量：
  - `GEMINI_API_KEY`
  - `GCP_LLM_MODEL`
  - `GCP_EMBEDDING_MODEL`

验收：

- 业务代码不直接依赖具体模型 SDK。
- embedding 和 LLM 都通过 model client 调用。
- LLM 使用 GCP GenAI 原生 streaming。
- 后端负责把 stream event 转换为 SSE 文本片段。
- 数据记录包含 `provider = gcp`、`model`、`embedding_model`。

### 1.5 `feature/rag-chat-integration`

目标：把 RAG 接入聊天生成。

范围：

- 用户问题生成 query embedding。
- 双路检索 top 3。
- 拼接 runtime context。
- 通过 GCP GenAI model client 调用 LLM。
- 后端以 SSE 分段返回回复。

验收：

- 输入测试句能召回相关教材/语法片段。
- Agent 回复能参考检索结果。

### 1.6 `feature/prompt-workflows`

目标：压缩并模块化 sys prompt。

范围：

- 从 `prompt/chatbot_sys_prompt.md` 提炼 kernel prompt。
- 新增 workflow prompts。
- 根据 problem type 动态选择 workflow。

建议文件：

```text
prompt/kernel.md
prompt/workflows/vocabulary_question.md
prompt/workflows/grammar_question.md
prompt/workflows/sentence_correction.md
prompt/workflows/reading_question.md
prompt/workflows/expression_naturalness.md
```

验收：

- 不再每轮注入 680 行完整 prompt。
- 不同问题类型加载不同 workflow。

### 1.7 `feature/mvp-validation`

目标：完成最小验收和文档更新。

范围：

- 加入 4-5 个手动验收样例。
- 记录测试结果。
- 更新 README 启动说明。

验收：

- README 能指导本地启动。
- 四个核心用例通过人工验收。

## 2. 推荐实现顺序

两小时内推荐顺序：

```text
1. bootstrap-fastapi-chat
2. sqlite-chat-logs
3. rag-embedding-index
4. gcp-model-client
5. rag-chat-integration
6. prompt-workflows
7. mvp-validation
```

如果时间不足，必须优先保证：

- 页面能聊天。
- embedding RAG 能跑。
- sys prompt 已压缩为 kernel + workflow。
- 聊天和检索记录能保存。

可以延后：

- 精细化教材 parser。
- 精细化词汇 exact lookup。
- 自动错误统计。
- 每周报告。
- 多用户。
- 登录。
- 教师后台。
