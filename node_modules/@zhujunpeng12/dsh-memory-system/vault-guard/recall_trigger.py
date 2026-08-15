"""Pure trigger policy shared by the hook and cold recall CLI."""
from __future__ import annotations

import re


ACK_RE = re.compile(r"^\s*(好的?|好|ok(?:ay)?|收到|谢谢|感谢|结束|先这样|嗯|行)[。.!！?？\s]*$", re.I)
TRIGGERS = (
    ("history-reference", re.compile(r"上次|之前|以前|过去|历史|继续|还记得|记得|当时|曾经|前面|刚才|上回")),
    ("evidence-request", re.compile(r"依据|证据|来源|原始记录|原始步骤|复盘轨迹|会话日志|为什么这样决定")),
    ("correction", re.compile(r"不对|不是这个|还没(?:有)?|没有修完|之前说过|你忘了|漏了|未覆盖")),
    ("memory-entity", re.compile(r"冷层|热包|记忆系统|Vault|AGENTS\.md|rules?\.md|events?|规则\s*\d+[a-z]?", re.I)),
    ("dated-reference", re.compile(r"20\d{2}[年./-]\d{1,2}(?:[月./-]\d{1,2}日?)?")),
)

SUBJECT_NOISE_RE = re.compile(
    r"继续|上次|之前|以前|过去|历史|还记得|记得|当时|曾经|前面|刚才|上回|"
    r"依据|证据|来源|原始记录|原始步骤|复盘轨迹|会话日志|为什么这样决定|"
    r"不对|不是这个|还没有|没有修完|之前说过|你忘了|漏了|未覆盖|"
    r"冷层|冷召回|cold\s*recall|热包|记忆系统|vault(?:-bootstrap|-cold-recall)?|AGENTS\.md|rules?\.md|events?|"
    r"请|只做|只读|验证|复测|说明|是否|收到|列出|本轮|上下文|回答|不要|再|打开|正文|"
    r"调用|工具|修改|任何|文件|写入|重复|注入|前三条|trace|files|chunks|candidates|selected|elapsed",
    re.I,
)


def trigger_reasons(query: str) -> list[str]:
    text = " ".join((query or "").strip().split())
    if not text or ACK_RE.fullmatch(text):
        return []
    return [name for name, pattern in TRIGGERS if pattern.search(text)]


def has_recall_subject(query: str) -> bool:
    """Require a concrete topic in addition to a recall-shaped instruction."""
    text = " ".join((query or "").strip().split())
    stripped = SUBJECT_NOISE_RE.sub("", text)
    stripped = re.sub(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]+", "", stripped, flags=re.I)
    return len(stripped) >= 3


def should_cold_recall(query: str) -> bool:
    return bool(trigger_reasons(query)) and has_recall_subject(query)
