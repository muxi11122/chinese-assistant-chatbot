# RAG 结构化切分与检索策略

本文档定义中文学习 Agent MVP 的 RAG 语料切分、metadata 设计和检索策略。目标是保证教材内容不会被固定字符数粗暴切碎，并且在用户围绕课文、题目或答案提问时，系统能够召回对应的原文、题目、选项和参考答案。

## 1. 核心原则

RAG chunk 的边界必须优先跟随教材语义结构，而不是字符数。

优先级：

```text
文章标题 > 自然段 > 注释 > 题型 section > 题目块 > 选项 > 参考答案 > 长度兜底
```

禁止：

- 把不同文章切进同一个 chunk。
- 把一道题的题干和选项切散。
- 把参考答案与题号关系丢失。
- 只按固定字符数切完整教材。
- 让 chunk 失去来源文章、题号、section 等 metadata。

允许：

- 对过长自然段做二次切分。
- 对过长文章段落按句号、问号、分号或换行切分。
- 对题目块整体保留，即使略长于普通 chunk。

## 2. 语料范围

教材教学范本：

```text
知识库min/教材/发展汉语高级阅读1.md
知识库min/教材/发展汉语高级阅读1参考答案.md
```

能力边界：

```text
知识库min/水平表/HSK4词汇.md
知识库min/水平表/HSK5词汇.md
知识库min/水平表/HSK语法1-5.md
```

本文重点覆盖教材教学范本的结构化切分和联动检索。当前实现也包含 `level_index` 的结构化切分：HSK4 词汇按 15 个词一组，HSK5 词汇按 8 个词一组，HSK 语法按单个语法项目切分。

### 2.1 当前 level_index 切分策略

`level_index` 的目标不是把词典逐条背诵式塞给模型，而是给回答提供“能力边界”和“可解释范围”。因此当前实现采用更粗的教学分组：

- `HSK4词汇.md`：每 15 个词汇条目生成一个 `vocabulary_group` chunk。
- `HSK5词汇.md`：每 8 个词汇条目生成一个 `vocabulary_group` chunk。
- `HSK语法1-5.md`：按表格中的单个语法项目生成一个 `grammar` chunk。

词汇分组 chunk 保留：

```json
{
  "index_name": "level_index",
  "chunk_type": "vocabulary_group",
  "level": "HSK4",
  "item_no_start": 1,
  "item_no_end": 15,
  "group_no": 1,
  "group_size": 15,
  "terms": ["..."]
}
```

语法 chunk 保留：

```json
{
  "index_name": "level_index",
  "chunk_type": "grammar",
  "hsk_level": "4",
  "grammar_point": "时间状语",
  "structure": "主语 + 时间 + 动词",
  "examples": ["..."]
}
```

这样做的取舍是：减少过碎 chunk 和 embedding 调用量，同时让检索结果提供足够的同级别词汇/语法边界。后续如果需要更精确的词条 exact lookup，可以在 embedding 检索之外增加关键词查表，不必把每个词都独立 embedding。

## 3. 教材结构识别

### 3.1 文章标题

识别：

```regex
^# 文章[一二三四五六七八九十]+
```

示例：

```text
# 文章一 休闲与游戏
```

metadata：

```json
{
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "chunk_type": "article_title"
}
```

### 3.2 自然段

识别：

```regex
^\[\d+\]
```

示例：

```text
[1] 如今，“休闲”已成为我们每个人生活中的重要内容。
```

metadata：

```json
{
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "chunk_type": "paragraph",
  "paragraph_no": 1
}
```

### 3.3 注释

识别：

```regex
^[①②③④⑤⑥⑦⑧⑨⑩]
```

示例：

```text
①高瞻远瞩（gāo zhān yuǎn zhǔ）：形容眼光远大。
```

metadata：

```json
{
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "chunk_type": "note",
  "note_no": "①",
  "term": "高瞻远瞩"
}
```

两小时 MVP 可以先把注释作为独立 chunk。后续可增强为段落和注释双向关联。

### 3.4 题型 section

识别：

```regex
^(一、|二、|三、|四、|五、|##\s*[一二三四五六七八九十]、)
```

示例：

```text
## 二、根据文章内容选择正确答案。（从A B C D四个选项中选择一个最佳答案）
```

metadata：

```json
{
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "chunk_type": "exercise_section",
  "section_id": "section_002",
  "section_title": "二、根据文章内容选择正确答案"
}
```

### 3.5 题目块

题目块必须尽量整体保存，包括题干和选项。

题号识别：

```regex
^\d+[.．、]
```

选项识别：

```regex
^[A-D][.．、]
```

示例 chunk：

```text
1.“休闲的价值不言而喻”，这句话的意思是：（）
A. 休闲可以用很多词语比喻
B. 休闲的作用不说也很明白
C. 休闲是一种不需要用语言的活动
D. 休闲的价值没有什么可说的
```

metadata：

```json
{
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "chunk_type": "question_block",
  "section_id": "section_002",
  "section_title": "二、根据文章内容选择正确答案",
  "question_no": "1",
  "has_options": true
}
```

### 3.6 参考答案

参考答案来自：

```text
知识库min/教材/发展汉语高级阅读1参考答案.md
```

答案必须解析出：

- 所属文章。
- 所属 section。
- 题号。
- 答案内容。

metadata：

```json
{
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "chunk_type": "answer",
  "section_id": "section_002",
  "question_no": "1",
  "answer": "B"
}
```

如果参考答案文件只提供简略答案，仍然必须保留题号映射。无法确定 section 时，至少保留 `article_id`、`article_title` 和 `question_no`。

## 4. Chunk Metadata 标准

所有教材 chunk 都必须包含以下基础字段：

```json
{
  "id": "chunk id",
  "index_name": "teaching_index",
  "source": "知识库min/教材/发展汉语高级阅读1.md",
  "source_file": "发展汉语高级阅读1.md",
  "chunk_type": "paragraph | note | exercise_section | question_block | answer",
  "article_id": "article_001",
  "article_title": "文章一 休闲与游戏",
  "section_id": "section_002",
  "section_title": "二、根据文章内容选择正确答案",
  "paragraph_no": null,
  "question_no": null,
  "content": "...",
  "content_hash": "..."
}
```

可选字段：

```json
{
  "option_labels": ["A", "B", "C", "D"],
  "answer": "B",
  "note_no": "①",
  "term": "高瞻远瞩",
  "linked_chunk_ids": ["..."]
}
```

## 5. 结构化切分流程

### 5.1 教材正文切分

流程：

```text
读取 markdown
→ 按行扫描
→ 识别当前 article
→ 识别当前 section
→ 遇到自然段生成 paragraph chunk
→ 遇到注释生成 note chunk
→ 遇到题型标题更新 section
→ 遇到题号收集题干和选项，生成 question_block chunk
→ 对过长 chunk 做句子级二次切分
```

关键要求：

- 每个 chunk 必须继承当前 `article_id` 和 `article_title`。
- 每个题目必须继承当前 `section_id` 和 `section_title`。
- 选项必须与题干合并为一个 `question_block`。
- 不要把同一个题目的 A/B/C/D 分成多个 chunk。

### 5.2 参考答案切分

流程：

```text
读取参考答案 markdown
→ 识别当前 article
→ 识别当前 section
→ 识别题号和答案
→ 生成 answer chunk
→ 建立 answer 与 question_block 的关联
```

如果参考答案格式较简略，允许先通过顺序和题号与正文题目对齐。

最低要求：

```text
article_id + question_no
```

更好要求：

```text
article_id + section_id + question_no
```

## 6. 知识点驱动的教材 Reference 检索

本产品不是练习册答疑工具。用户通常不会直接问“第几题选什么”或“为什么选 B”。用户更常见的问题是：

- 某个词是什么意思，怎么用。
- 某个句子为什么不自然。
- 自己造的句子哪里有问题。
- 两个表达有什么区别。
- 某个语法结构应该怎么理解。

因此，检索主流程必须是：

```text
用户问题
→ analyze 出语言 point
→ 基于 point 检索教材 reference
→ 基于 point 检索水平边界
→ 生成不超纲的引导式回答
```

教材中的文章、题目和参考答案都只是 teaching reference。它们用于帮助 Agent 找到教材中的表达、例句、考查方式和解释角度，而不是把产品变成“题目答案助手”。

### 6.1 两阶段检索

检索分两步：

```text
Step 1: Point Analysis
识别 problem_type、language_point、query_terms、possible_error_type。

Step 2: Reference Retrieval
用 language_point 和 query_terms 去检索 paragraph、note、question_block、answer。
```

内部分析字段：

```json
{
  "problem_type": "vocabulary | grammar | sentence_correction | expression_naturalness | reading_expression",
  "language_point": "时间状语位置",
  "query_terms": ["早上", "时间", "位置"],
  "student_sentence": "我昨天去图书馆学习中文早上。",
  "retrieval_intent": "find_teaching_reference"
}
```

### 6.2 词汇问题

示例：

```text
“方便”是什么意思？
```

策略：

1. analyze 出 `language_point = 方便`，`problem_type = vocabulary`。
2. 检索教材 paragraph、note 中包含或语义接近“方便”的片段。
3. 检索 question_block 时，只把题目当作该词的使用场景。
4. 不主动返回参考答案，除非答案解释中包含有用的表达范本。
5. 同时检索 level_index 判断“方便”是否在 HSK4/HSK5 范围内。

返回 context：

```text
教材原文/注释中的用法 top 2
教材题目中的使用场景 top 1，可选
词汇边界 top 1
```

### 6.3 语法问题

示例：

```text
为什么“早上”不能放在最后？
```

策略：

1. analyze 出 `language_point = 时间状语位置`。
2. 检索 level_index 中相关语法边界。
3. 检索教材 paragraph 中相似时间状语结构。
4. 如果教材 question_block 中出现相同结构，可作为“考查范本”补充。
5. 不以题目答案作为主要依据。

返回 context：

```text
水平表语法边界 top 2
教材相似句 top 2
相关题目范本 top 1，可选
```

### 6.4 句子纠错

示例：

```text
我昨天去图书馆学习中文早上。
```

策略：

1. analyze 出错误点：`早上` 的位置。
2. 检索 level_index 中“时间状语/状语位置”。
3. 检索教材 paragraph 中类似“时间 + 动作”的句子。
4. 生成回答时优先引导学生观察位置，不第一次直接给完整答案。
5. 教材 reference 只用于提供类似例句，不要求显式告诉学生“教材第几题”。

返回 context：

```text
能力边界：时间状语位置
教材相似例句 top 2
```

### 6.5 表达自然度/搭配问题

示例：

```text
我可以说“我方便明天去”吗？
```

策略：

1. analyze 出 `language_point = 方便的用法 / 时间位置 / 表达自然度`。
2. 检索教材中“方便”或相近表达。
3. 检索水平表判断可解释范围。
4. 如果教材题目中有类似表达，作为 usage reference。
5. 回复中帮助学生判断为什么不自然，并引导其调整。

返回 context：

```text
教材用法 reference top 2
能力边界 top 2
```

### 6.6 题目和答案的正确用途

教材中的 `question_block` 和 `answer` 仍然要结构化保存，但用途是：

- 帮助 Agent 理解教材如何考查某个词汇、语法或表达。
- 为 Agent 提供教学范本和迁移练习灵感。
- 当用户贴出的内容刚好来自教材题目时，帮助 Agent 找到完整上下文。

默认不做：

- 不把用户问题当成“求答案”。
- 不主动输出“这题选 B”。
- 不把参考答案作为最终回答核心。
- 不把产品定位成练习册解析器。

当检索命中 `question_block` 时，metadata expansion 应是“可选扩展”，不是强制扩展：

```text
召回 question_block
→ 判断它是否有助于解释当前 language_point
→ 有帮助才补充 answer 或相关 paragraph
→ prompt 中标记为“教材练习范本”，而不是“学生当前题目”
```

## 7. Retriever 返回格式

retriever 不应只返回纯文本，应返回结构化 context。

示例：

```json
{
  "teaching_context": {
    "detected_point": "时间状语位置",
    "paragraphs": [
      {
        "chunk_id": "p_article_001_001",
        "content": "[1] 如今，“休闲”已成为...",
        "reference_role": "example_sentence"
      }
    ],
    "notes": [],
    "exercise_references": [
      {
        "chunk_id": "q_article_001_section_002_001",
        "content": "1.“休闲的价值不言而喻”...",
        "reference_role": "teaching_pattern"
      }
    ],
    "answer_references": []
  }
}
```

然后 prompt 中可以拼成：

```text
[本轮语言点]
...

[相关原文]
...

[教材练习范本，可选]
...
```

## 8. 关联策略

### 8.1 主键设计

建议 chunk id 可读、稳定：

```text
article_001_title
article_001_p_001
article_001_note_001
article_001_section_002_q_001
article_001_section_002_a_001
```

### 8.2 关联字段

题目与答案通过以下字段关联：

```text
article_id
section_id
question_no
```

如果 `section_id` 缺失，降级为：

```text
article_id
question_no
```

### 8.3 Expansion 规则

召回某类 chunk 后自动扩展：

```text
召回 paragraph → 可补充同 article 的相关 note
召回 note → 补充同 article 的相关 paragraph
召回 question_block → 仅当有助于解释当前 language_point 时，补充 answer 或相关 paragraph
召回 answer → 仅作为教材范本参考，必须回到对应 question_block 或 paragraph 判断其教学用途
```

注意：`question_block` 和 `answer` 的 expansion 是 reference expansion，不是练习答案解析。最终回答必须围绕用户的知识性问题和语言点展开。

## 9. 长度兜底

结构化 chunk 仍需控制长度，但长度不能破坏结构。

建议：

```text
paragraph chunk：300-1000 中文字符
question_block：完整题目优先，可到 1200 中文字符
answer chunk：通常不切
note chunk：通常不切
```

如果自然段超过 1000 中文字符：

1. 优先按句号、问号、叹号、分号切。
2. 保留同一个 `paragraph_no`。
3. 添加 `part_no`。

示例：

```json
{
  "chunk_type": "paragraph",
  "paragraph_no": 3,
  "part_no": 1
}
```

禁止因为长度兜底把两个题目合并，或把一个题目的选项切到另一个 chunk。

## 10. 最小验收测试

### 10.1 结构切分验收

对 `发展汉语高级阅读1.md` 运行 chunker 后，检查：

- 每篇文章有独立 `article_id`。
- 每个 `[1]`、`[2]` 等自然段生成 paragraph chunk。
- 每个 `①`、`②` 等注释生成 note chunk。
- 每道选择题题干和 A/B/C/D 在同一个 question_block chunk。
- 不同文章的内容不会进入同一个 chunk。

### 10.2 答案关联验收

对 `发展汉语高级阅读1参考答案.md` 运行 chunker 后，检查：

- 每个 answer chunk 有 `article_id`。
- 每个 answer chunk 有 `question_no`。
- 可以通过 `article_id + section_id + question_no` 找回对应 question_block。
- 若 `section_id` 不可用，可以通过 `article_id + question_no` 找回候选题目。

### 10.3 知识点检索验收

输入：

```text
我昨天去图书馆学习中文早上。
```

retriever 必须返回：

- 检测到的语言点：时间状语位置。
- level_index 中相关语法边界。
- teaching_index 中相似时间表达或句式 reference。
- 不应返回“某题答案”为主上下文。

输入：

```text
“方便”是什么意思？
```

retriever 必须返回：

- 检测到的语言点：词义/用法。
- 教材中相关用法或相似表达 reference。
- HSK4/HSK5 词汇边界。
- 如召回题目，只能作为教材范本，不应触发答案解析。

输入：

```text
我可以说“我方便明天去”吗？
```

retriever 必须返回：

- 检测到的语言点：表达自然度、方便的用法、时间位置。
- 教材相似表达 reference。
- 水平边界 reference。
- 不应返回“正确选项/参考答案”为主上下文。

## 11. 实现优先级

两小时 MVP 优先级：

1. 正文 parser：article、paragraph、note、section、question_block。
2. 答案 parser：article、section、question_no、answer。
3. metadata 入库。
4. embedding 检索。
5. knowledge-point analysis。
6. reference-oriented metadata expansion。
7. prompt context 拼接。

可以延后：

- 注释与段落精确 linking。
- 复杂题型深度解析。
- 多答案题完整评分逻辑。
- 自动判断学生答案是否完全正确。

## 12. 完成定义

本 RAG 策略完成时，应满足：

- 教材按结构切分，而不是按固定字符粗切。
- 题目与选项作为完整 question_block 保存。
- 参考答案能通过 metadata 与题目关联。
- 用户提出知识性问题时，retriever 先围绕语言点召回教材 reference。
- 题目和答案只作为教学范本，不作为默认答题主流程。
- 检索到题目或答案时，系统能判断其是否服务于当前 language_point。
- Agent 拿到的是“语言点 + 教材参考 + 能力边界”，而不是“题目 + 答案解析”。
