# chinese-assistant-chatbot

中文学习 Agent MVP 的本地 demo。当前分支 `feature/rag-chat-integration` 已跑通“网页聊天 + GCP 流式回复 + 双路 RAG + SQLite/文件 trace”的最小闭环。

## 当前 Demo 能力

- FastAPI 后端和静态聊天前端。
- `GET /api/health` 健康检查。
- `POST /api/chat` SSE 流式聊天。
- 用户问题先分析 `problem_type` / `language_point`。
- 用户问题生成 query embedding。
- 检索 `teaching_index` top 3。
- 检索 `level_index` top 3。
- 基于 `language_point + 用户问题` 检索教材 reference。
- 拼接 kernel prompt、workflow prompt、runtime context 和 RAG reference。
- 通过 `app/model_client.py::stream_reply` 调用 GCP GenAI stream。
- 后端把 GCP stream event 转成 SSE `token` event 给前端。
- SQLite 保存 conversations、messages、agent_events、knowledge index。
- `chat_logs/` 同步保存 JSONL 和完整 RAG chat trace，方便 debug。

产品定位上，这个 demo 不是练习册答疑助手。教材题目和参考答案只作为 teaching reference；回答应围绕用户知识性问题和本轮 `language_point`，全中文、不过度超纲，并优先引导学生观察和修正。

## 环境准备

推荐使用 `uv`：

```bash
uv sync
```

如果不用 `uv`，也可以使用项目虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

配置 GCP GenAI 环境变量：

```bash
export GEMINI_API_KEY=...
export GCP_LLM_MODEL=gemini-3.8-flash
export GCP_EMBEDDING_MODEL=gemini-embedding-2
```

当前代码已移除聊天主链路中的 mock fallback。缺少依赖、key、模型名或网络不可用时，会直接报错，避免 demo 看起来在运行但其实没有走真实模型。

## 构建 RAG Index

第一次真实测试前，先构建当前 GCP embedding 模型对应的双路索引：

```bash
uv run python scripts/verify_rag_index.py
```

常用查询验证：

```bash
uv run python scripts/verify_rag_index.py --top-k 5 --query "我昨天去图书馆学习中文早上。"
```

默认会复用已有 source/model 的 embedding 缓存。只有当源文件、切分逻辑、embedding model 变化，或你明确想全量重建时，才使用：

```bash
uv run python scripts/verify_rag_index.py --force
```

索引构建会在每个 embedding batch 成功后立即写入 SQLite；如果中途网络中断，下一次运行会复用已经成功落库的 batch，并继续补剩余 chunks。

当前切分策略：

- `teaching_index`：教材正文、注释、题型 section、question_block、answer。
- `level_index`：HSK4 词汇每 15 个词一组；HSK5 词汇每 8 个词一组；HSK 语法按单个语法项目切分。
- 当前 demo 量级约为 `teaching_index=143`、`level_index=155`，以本地 SQLite 实际结果为准。

## 启动 Demo

启动后端和前端：

```bash
uv run uvicorn app.main:app --reload
```

打开：

```text
http://127.0.0.1:8000
```

网页端输入中文问题后，应看到用户气泡、状态提示、Agent 气泡逐段出现、发送中状态和自动滚动。

## API 验证

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

聊天流式接口：

```bash
curl -N \
  -H "Content-Type: application/json" \
  -d '{"message":"“方便”是什么意思？","history":[]}' \
  http://127.0.0.1:8000/api/chat
```

预期看到多段 SSE event，包括 `metadata`、`status`、`token` 和 `done`。

## RAG 聊天链路验证

本地最小集成验证：

```bash
uv run python scripts/verify_rag_chat_integration.py
```

脚本使用真实 query embedding 和已有 RAG index，并在脚本内替换 LLM stream，验证：

- analyze 得到 `problem_type` / `language_point`。
- 检索 `teaching_index` top 3 和 `level_index` top 3。
- runtime context 包含 Teaching RAG / Level Boundary RAG。
- 保存 `rag_context_built`、`rag_chat_completed`、`rag_trace_written` agent events。
- 保存 assistant message，并记录 provider/model/embedding_model。
- 写入完整 trace 文件。

## Debug 和 Trace

运行数据位置：

- SQLite：`data/app.sqlite3`
- 每日 JSONL：`chat_logs/YYYY-MM-DD.jsonl`
- 完整 trace：`chat_logs/traces/YYYY-MM-DD/{conversation_id}_{timestamp}.json`

完整 trace 包含：

- conversation id、user message、history。
- analyze 结果。
- retrieval query。
- 当前 index counts。
- query embedding provider/model/dimensions。
- teaching/level top-k chunk ids、metadata、content、score。
- prompt bundle、runtime context、final prompt。
- model provider/model。
- assistant final reply。
- 前端可见的 event payload。

当网页卡住或回答不符合预期时，优先看同一个 `conversation_id` 对应的 trace 文件，再对照 SQLite 的 `agent_events`。

## Prompt 调整入口

当前 prompt 已拆分：

```text
prompt/kernel.md
prompt/workflows/vocabulary_question.md
prompt/workflows/grammar_question.md
prompt/workflows/sentence_correction.md
prompt/workflows/reading_expression.md
prompt/workflows/expression_naturalness.md
```

如果回答太像列表或题解，优先调整：

- `prompt/kernel.md`：全局风格、是否允许 Markdown、是否默认分点。
- 对应 workflow：某一类问题的组织方式和引导节奏。

当前默认要求是：自然段串联解释，除非用户明确要求清单，否则避免编号列表和 Markdown 星号。

## 当前限制

- 检索仍使用 SQLite 全量扫描余弦相似度，适合当前最小知识库，不是生产级向量库。
- analyze 仍是轻量规则，不是独立模型调用。
- 水平边界只覆盖当前 `知识库min/水平表` 文件。
- 自动化端到端测试依赖本地 GCP key、网络和已构建索引；遇到网络或代理问题时需要先完成环境排查。
