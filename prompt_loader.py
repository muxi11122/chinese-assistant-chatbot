from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent
PROMPT_DIR = ROOT_DIR / "prompt"
WORKFLOW_DIR = PROMPT_DIR / "workflows"

DEFAULT_STUDENT_PROFILE = {
    "母语": "印尼语",
    "当前水平": "HSK4",
    "目标水平": "HSK5",
    "核心教材": "发展汉语高级阅读1",
}

WORKFLOW_FILES = {
    "vocabulary_question": "vocabulary_question.md",
    "grammar_question": "grammar_question.md",
    "sentence_correction": "sentence_correction.md",
    "reading_expression": "reading_expression.md",
    "expression_naturalness": "expression_naturalness.md",
}

PROBLEM_TYPE_PRIORITY = (
    "sentence_correction",
    "expression_naturalness",
    "reading_expression",
    "grammar_question",
    "vocabulary_question",
)

KEYWORDS = {
    "sentence_correction": (
        "对吗",
        "有没有错",
        "哪里错",
        "怎么改",
        "帮我改",
        "改一下",
        "纠正",
        "修改",
    ),
    "expression_naturalness": (
        "哪个更自然",
        "更自然",
        "自然吗",
        "可以这样说吗",
        "这样说可以吗",
        "区别",
        "有什么不同",
        "一样吗",
    ),
    "reading_expression": (
        "课文",
        "文章",
        "原文",
        "这句话怎么理解",
        "这句怎么理解",
        "阅读",
        "段落",
    ),
    "grammar_question": (
        "为什么",
        "语法",
        "结构",
        "句式",
        "怎么连接",
        "放在哪里",
    ),
    "vocabulary_question": (
        "是什么意思",
        "什么意思",
        "怎么用",
        "怎么说",
        "这个词",
        "词语",
        "近义词",
    ),
}


def detect_problem_type(message: str) -> str:
    normalized = "".join(message.lower().split())
    if not normalized:
        return "vocabulary_question"

    matches = {
        problem_type
        for problem_type, keywords in KEYWORDS.items()
        if any(keyword in normalized for keyword in keywords)
    }
    if not matches and looks_like_sentence_for_correction(message):
        matches.add("sentence_correction")

    for problem_type in PROBLEM_TYPE_PRIORITY:
        if problem_type in matches:
            return problem_type
    return "vocabulary_question"


def looks_like_sentence_for_correction(message: str) -> bool:
    stripped = message.strip()
    if len(stripped) < 8:
        return False
    sentence_marks = ("。", "？", "?", "！", "!")
    common_sentence_words = ("我", "你", "他", "她", "我们", "他们", "昨天", "今天", "明天")
    return stripped.endswith(sentence_marks) and any(word in stripped for word in common_sentence_words)


def load_kernel_prompt() -> str:
    return _read_prompt_file(PROMPT_DIR / "kernel.md")


def load_workflow_prompt(problem_type: str) -> str:
    normalized = normalize_problem_type(problem_type)
    return _read_prompt_file(WORKFLOW_DIR / WORKFLOW_FILES[normalized])


def normalize_problem_type(problem_type: str | None) -> str:
    if not problem_type:
        return "vocabulary_question"
    aliases = {
        "vocabulary": "vocabulary_question",
        "grammar": "grammar_question",
        "correction": "sentence_correction",
        "sentence": "sentence_correction",
        "reading": "reading_expression",
        "naturalness": "expression_naturalness",
    }
    normalized = aliases.get(problem_type, problem_type)
    if normalized not in WORKFLOW_FILES:
        return "vocabulary_question"
    return normalized


def build_runtime_context(
    message: str,
    *,
    problem_type: str | None = None,
    history: Sequence[Mapping[str, str]] | None = None,
    teaching_refs: Sequence[Mapping[str, Any] | str] | None = None,
    level_refs: Sequence[Mapping[str, Any] | str] | None = None,
    student_profile: Mapping[str, str] | None = None,
    detected_point: Mapping[str, str] | None = None,
) -> str:
    resolved_type = normalize_problem_type(problem_type) if problem_type else detect_problem_type(message)
    profile = student_profile or DEFAULT_STUDENT_PROFILE
    sections = [
        "[Student Profile Summary]",
        _format_mapping(profile),
        "",
        "[Recent History]",
        _format_history(history or []),
        "",
        "[Detected Point]",
        _format_mapping({"problem_type": resolved_type, **(detected_point or {})}),
        "",
        "[Teaching RAG]",
        _format_references(teaching_refs or []),
        "",
        "[Level Boundary RAG]",
        _format_references(level_refs or []),
        "",
        "[Current Task]",
        message.strip(),
    ]
    return "\n".join(sections).strip()


def build_prompt(
    message: str,
    *,
    problem_type: str | None = None,
    history: Sequence[Mapping[str, str]] | None = None,
    teaching_refs: Sequence[Mapping[str, Any] | str] | None = None,
    level_refs: Sequence[Mapping[str, Any] | str] | None = None,
    student_profile: Mapping[str, str] | None = None,
    detected_point: Mapping[str, str] | None = None,
) -> str:
    bundle = build_prompt_bundle(
        message,
        problem_type=problem_type,
        history=history,
        teaching_refs=teaching_refs,
        level_refs=level_refs,
        student_profile=student_profile,
        detected_point=detected_point,
    )
    return bundle["prompt"]


def build_prompt_bundle(
    message: str,
    *,
    problem_type: str | None = None,
    history: Sequence[Mapping[str, str]] | None = None,
    teaching_refs: Sequence[Mapping[str, Any] | str] | None = None,
    level_refs: Sequence[Mapping[str, Any] | str] | None = None,
    student_profile: Mapping[str, str] | None = None,
    detected_point: Mapping[str, str] | None = None,
) -> dict[str, str]:
    resolved_type = normalize_problem_type(problem_type) if problem_type else detect_problem_type(message)
    kernel = load_kernel_prompt()
    workflow = load_workflow_prompt(resolved_type)
    runtime_context = build_runtime_context(
        message,
        problem_type=resolved_type,
        history=history,
        teaching_refs=teaching_refs,
        level_refs=level_refs,
        student_profile=student_profile,
        detected_point=detected_point,
    )
    prompt = "\n\n".join(
        [
            kernel,
            workflow,
            "# Runtime Context",
            runtime_context,
        ]
    )
    return {
        "problem_type": resolved_type,
        "kernel": kernel,
        "workflow": workflow,
        "runtime_context": runtime_context,
        "prompt": prompt,
    }


def _read_prompt_file(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _format_mapping(values: Mapping[str, str]) -> str:
    if not values:
        return "无"
    return "\n".join(f"- {key}：{value}" for key, value in values.items() if value)


def _format_history(history: Sequence[Mapping[str, str]], max_turns: int = 6) -> str:
    if not history:
        return "无"
    clipped = history[-max_turns:]
    lines = []
    for item in clipped:
        role = item.get("role", "unknown")
        content = " ".join(item.get("content", "").split())
        if content:
            lines.append(f"- {role}：{content}")
    return "\n".join(lines) if lines else "无"


def _format_references(references: Iterable[Mapping[str, Any] | str]) -> str:
    lines = []
    for index, reference in enumerate(references, start=1):
        if isinstance(reference, str):
            content = reference.strip()
            heading = ""
        else:
            heading = str(reference.get("heading") or reference.get("source") or "").strip()
            content = str(reference.get("content") or "").strip()
        if not content:
            continue
        prefix = f"{index}. "
        if heading:
            prefix += f"{heading}："
        lines.append(prefix + content)
    return "\n".join(lines) if lines else "无"
