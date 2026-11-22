"""把合成原始记录（如 data/hearings.json）导入登记簿。

原始记录只提供 id/kind/version/text；立场的判定、方案争点的关联由
承办人员通过 HearingStore 的结构化接口完成，工具不替人下结论。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Union

from .models import StatementKind
from .store import HearingStore

#: 原始记录 kind → 发言类别
KIND_MAP = {
    "resident_statement": StatementKind.ORAL,
    "written_comment": StatementKind.WRITTEN,
    "agency_reply": StatementKind.AGENCY,
    "minutes_correction": StatementKind.NOTE,
}

#: 原始记录 kind → 发言人角色
ROLE_MAP = {
    "agency_reply": "agency",
    "minutes_correction": "staff",
}


def load_source_records(path: Union[str, Path], store: HearingStore) -> dict[str, str]:
    """把原始记录导入为发言（保留版本关系），返回 {记录 id: 发言 id}。

    同一记录 id 的多个版本按 version 升序导入：首个版本建立发言，
    后续版本作为更正追加。
    """
    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("原始记录应为 JSON 数组")
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["id"], []).append(record)

    mapping: dict[str, str] = {}
    for record_id, versions in groups.items():
        versions.sort(key=lambda r: r.get("version", 1))
        first = versions[0]
        kind = KIND_MAP.get(first.get("kind"), StatementKind.NOTE)
        role = ROLE_MAP.get(first.get("kind"), "resident")
        speaker = store.add_speaker(
            name=f"来源记录 {record_id}", role=role, speaker_id=f"src-{record_id}"
        )
        statement = store.add_statement(
            speaker.id,
            kind,
            first["text"],
            note=f"来源版本 {first.get('version', 1)}",
        )
        mapping[record_id] = statement.id
        for record in versions[1:]:
            store.correct_statement(
                statement.id,
                record["text"],
                note=f"来源版本 {record.get('version', '?')}",
            )
    return mapping
