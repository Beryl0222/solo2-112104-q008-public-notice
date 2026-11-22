"""听证纪要：发布时冻结引用的文本与版本，之后的更正不影响已发布纪要。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MinutesItem:
    """纪要中的一条记录。

    category 区分内容性质：
    - ``opinion``：居民意见（不是部门决议）；
    - ``formal_response``：部门正式答复（已结束的回应）；
    - ``open_note``：待研究/待核对/进行中的事项（不是正式答复）。
    """

    category: str
    label: str           # 人类可读标签，如“居民意见（反对）”“部门正式答复（承诺）”
    ref_id: str          # 意见或回应的编号
    plan_id: str
    quoted_text: str     # 发布当时引用的文本
    quoted_version: int  # 发布当时引用的版本
    source: str          # 出处说明（发言/回应编号、版本与段落）
    proposer: str = ""   # 意见提出者（回应条目为空）
    unit: str = ""       # 责任单位（意见条目为空）


@dataclass(frozen=True)
class Minutes:
    """一份已发布的听证纪要，内容不可变。"""

    id: str
    title: str
    published_at: str
    items: tuple[MinutesItem, ...]

    def opinions(self) -> tuple[MinutesItem, ...]:
        return tuple(i for i in self.items if i.category == "opinion")

    def formal_responses(self) -> tuple[MinutesItem, ...]:
        return tuple(i for i in self.items if i.category == "formal_response")

    def open_notes(self) -> tuple[MinutesItem, ...]:
        return tuple(i for i in self.items if i.category == "open_note")
