# 公共听证意见与部门承诺的文本追踪

本项目整理公共规划听证的记录员要把现场发言、书面意见和部门答复交给后续承办人员。居民可能支持目标却反对某个方案，部门也会把“研究后答复”说成明确承诺；若按段落情感或关键词汇总，未采纳的建议会被写成决议，已承诺的回复也可能在意见更正后失去对应对象。

演练资料位于 `data/hearings.json`，内容为合成记录，保留不同来源的原始表述和修订关系。项目环境使用 Python 3.11 标准库。

## 工具能力（`hearing_kit` 包）

`hearing_kit` 提供可重复使用的听证文本整理能力，立场分类与方案关联由承办人员判定，工具负责结构化登记、版本追踪与视图汇总，不替代行政决策：

- **结构化意见**：发言中的支持、反对、附条件、理由、替代建议分别登记为 `Opinion`，各自关联具体方案、提出者与依据段落（`ParagraphRef` 固定到登记时的发言版本）。
- **部门回应分类**：解释说明、待研究（“带回去研究”“下次说明”）、明确承诺分属不同 `ResponseKind`；待研究不算正式答复，承诺记录责任单位。
- **更正与撤回**：发言或意见被更正、居民撤回意见时，未结束的回应自动标记“待重新核对”，须 `recheck_response` 后才能结束；已结束的答复与已发布纪要不受影响。
- **纪要快照**：`publish_minutes` 冻结发布当时引用的文本与版本；纪要条目区分“居民意见”“部门正式答复”“非正式答复（待研究/待核对）”。
- **不合并、不扩大**：不同居民的相似建议只能 `mark_related` 互相参照（`suggest_related` 仅作人工核对提示），绝不自动合并成联署意见；部门回应不得跨方案（`PlanScopeError`），只答复 `opinion_ids` 列出的意见。
- **承办视图**：`plan_view(plan_id)` 按方案列出尚待答复的问题、部门承诺、责任单位与依据段落原文；`statement_trace(statement_id)` 从原始发言追到方案争点及后续响应。
- **可持久化**：`store.save()` / `HearingStore.load()` 以 JSON 保存与恢复登记簿。

## 使用示例

```python
from hearing_kit import HearingStore

store = HearingStore()
plan = store.add_plan("方案甲：滨河步道拟议路线")
lin = store.add_speaker("林女士")
s1 = store.add_statement(lin.id, "oral",
    ["支持改善河岸通行，但不赞成方案甲把步道贴近学校围墙。", "建议比较方案乙。"])
o1 = store.add_opinion(s1.id, lin.id, plan.id, "condition",
                       "认可改善目标，但反对步道贴近学校围墙", paragraphs=(1,))
store.add_response(plan.id, [o1.id], "deferred", "带回去研究，下次说明", "市交通局")
view = store.plan_view(plan.id)        # 待答复清单、承诺、责任单位、依据段落
minutes = store.publish_minutes("第一次听证纪要")  # 区分意见与正式答复的快照
```

完整流程演示（合成材料）：`python3 examples/demo.py`。
原始记录导入：`load_source_records("data/hearings.json", store)` 把合成记录按版本导入为发言，之后的立场判定与关联由承办人员完成。

## 本地检查

```bash
python3 -m unittest discover -s tests -v
```
