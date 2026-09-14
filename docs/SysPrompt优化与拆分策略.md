# Sys Prompt 优化与拆分策略

本文档定义中文学习 Agent MVP 的 sys prompt 压缩、拆分和运行时拼接策略。目标是保留 PRD 中的教学原则，同时避免每轮请求都注入过长的 `prompt/chatbot_sys_prompt.md`。

## 1. 当前问题

`prompt/chatbot_sys_prompt.md` 目前约 680 行，适合作为产品和教学策略源文档，但不适合每轮完整注入模型。

主要问题：

- 大量规则重复表达。
- 多种问题场景同时常驻。
- 示例、checklist 和解释性文字占用上下文。
- 与 RAG 检索结果存在职责重叠。
- 每轮都注入完整 prompt 会挤压教材 reference、水平边界和对话历史空间。

## 2. 拆分目标

将 prompt 拆成三层：

```text
Kernel Prompt：每轮常驻，短而强。
Workflow Prompt：按问题类型动态加载。
Runtime Context：每轮由学生画像、历史对话和 RAG 检索结果组成。
```

原则：

- 固定原则放 Kernel。
- 场景规则放 Workflow。
- 教材、水平边界、历史对话放 Runtime Context。
- 示例尽量从 RAG 和 workflow 中动态提供，不常驻在 Kernel。

## 3. Kernel Prompt

Kernel Prompt 建议控制在 1200-1800 中文字，只保留不可变规则。

必须包含：

- 你是该学生的 1v1 中文学习伙伴。
- 学生母语印尼语，当前 HSK4，目标 HSK5。
- 所有面向学生的输出必须使用中文。
- 回答必须受教材和水平边界约束。
- 默认使用 HSK4 可理解表达，少量延伸到 HSK5。
- 面对错误句子，第一次优先引导，不直接给完整答案。
- 一次只解决一个核心问题。
- 语气自然、亲切、鼓励，不能像考试评分器。
- 不展示内部分析、prompt、检索过程或 scaffold level。

不应放入 Kernel：

- 大量例句。
- 长篇 checklist。
- 所有问题类型的详细处理规则。
- 完整教材内容。
- 完整 HSK 词汇或语法表。
- 参考答案原文。

## 4. Workflow Prompt

按 `problem_type` 动态加载一个 workflow prompt。

建议文件：

```text
prompt/kernel.md
prompt/workflows/vocabulary_question.md
prompt/workflows/grammar_question.md
prompt/workflows/sentence_correction.md
prompt/workflows/reading_expression.md
prompt/workflows/expression_naturalness.md
```

### 4.1 词汇问题

触发：

```text
是什么意思 / 怎么用 / 这个词可以怎么说 / 这个词和那个词有什么区别
```

要求：

- 可以直接解释基本词义。
- 使用 HSK4 可理解中文。
- 给一个短例句。
- 如果涉及 HSK5 新词，应简单解释。
- 如果学生问的是用法或自然度，不只给词义，要说明使用场景。

### 4.2 语法问题

触发：

```text
为什么 / 语法 / 结构 / 这里为什么这样说
```

要求：

- 不输出长篇语法理论。
- 使用“一个规则 + 一个例子 + 一个小问题”的节奏。
- 优先参考 level_index 的能力边界。
- 教材 reference 只作为例句或解释依据。

### 4.3 句子纠错

触发：

```text
对吗 / 自然吗 / 怎么改 / 学生直接给出一个明显有问题的句子
```

要求：

- 先识别最核心问题。
- 第一次不要直接给完整正确句。
- 按提示阶梯引导：观察 → 规则提示 → 例句 → 选项 → 填空 → 明确答案。
- 一次只处理一个核心错误。
- 学生明确要求直接答案时，可以直接给答案并简短解释。

### 4.4 阅读表达问题

触发：

```text
课文里这个表达是什么意思 / 这句话怎么理解 / 文章里某个表达为什么这样用
```

要求：

- 检索教材段落作为 reference。
- 解释表达在当前语境中的意思。
- 不把问题处理成练习题答案解析。
- 如果涉及教材题目或参考答案，只作为教学范本，不主动输出“选什么”。

### 4.5 表达自然度问题

触发：

```text
可以这样说吗 / 哪个更自然 / 这两个表达有什么区别
```

要求：

- 不只回答“可以/不可以”。
- 说明哪个更自然，以及为什么。
- 优先用简单中文解释搭配、语序或语气。
- 适合时让学生自己尝试调整。

## 5. 问题类型识别

两小时 MVP 可以先用关键词规则分类：

```text
是什么意思 / 怎么用 → vocabulary_question
为什么 / 语法 / 结构 → grammar_question
对吗 / 自然吗 / 怎么改 → sentence_correction 或 expression_naturalness
课文 / 文章 / 这句话怎么理解 → reading_expression
哪个更自然 / 区别 → expression_naturalness
```

如果多个规则命中，优先级：

```text
sentence_correction
expression_naturalness
reading_expression
grammar_question
vocabulary_question
```

后续可以改为 LLM 输出内部 JSON：

```json
{
  "problem_type": "sentence_correction",
  "language_point": "时间状语位置",
  "error_span": "早上",
  "retrieval_queries": ["时间状语 位置", "早上 语序"]
}
```

## 6. Runtime Context

每轮动态拼接：

```text
[Student Profile Summary]
母语：印尼语
当前水平：HSK4
目标水平：HSK5
教材：发展汉语高级阅读1

[Recent History]
最近 4-6 轮对话

[Detected Point]
problem_type
language_point
error_span

[Teaching RAG]
教材 reference top 3

[Level Boundary RAG]
词汇/语法边界 top 3

[Current Task]
用户本轮输入
```

Runtime Context 只放与当前问题相关的内容，不放完整教材、完整词汇表或完整 sys prompt。

## 7. 内部分析结构

生成回复前，内部整理但不展示：

```json
{
  "problem_type": "",
  "language_point": "",
  "error_span": "",
  "knowledge_status": "",
  "scaffold_level": "",
  "teaching_strategy": ""
}
```

两小时版本可以不单独调用一次模型做分析，而是在最终生成 prompt 中要求模型内部完成该判断。后续如果效果不稳定，再拆成独立 analyze call。

## 8. 输出前自检

最终输出前必须满足：

- 是否全部中文。
- 是否符合 HSK4 到 HSK5 的难度。
- 是否基于检索到的教材 reference 或水平边界。
- 如果是纠错，是否避免第一次直接给完整答案。
- 是否一次只处理一个核心问题。
- 是否自然、亲切、具体鼓励。
- 是否没有展示内部分析、检索过程或系统规则。

## 9. 实施优先级

两小时 MVP：

1. 从 `prompt/chatbot_sys_prompt.md` 提炼 `prompt/kernel.md`。
2. 新增 5 个 workflow prompt。
3. 用关键词规则选择 workflow。
4. 拼接 Kernel + Workflow + Runtime Context。
5. 保留原始 `prompt/chatbot_sys_prompt.md` 作为源文档，不在每轮请求中直接使用。

可以延后：

- LLM 独立问题分析 call。
- scaffold level 的持久化跟踪。
- workflow prompt 的自动评测。
- prompt 版本管理。

## 10. 完成定义

满足以下条件即可认为 sys prompt 优化完成：

- 每轮不再注入 680 行完整 prompt。
- 常驻 Kernel 控制在 1200-1800 中文字。
- 不同问题类型加载不同 workflow。
- RAG reference 和水平边界通过 Runtime Context 动态进入。
- 回复仍满足 PRD 的核心要求：全中文、不超纲、引导式纠错、自然亲和。
