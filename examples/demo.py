"""合成材料演示：一场河岸步道听证的整理流程。

运行方式（仓库根目录）：
    python3 examples/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hearing_kit import (
    RESPONSE_STATUS_LABELS,
    HearingStore,
    PlanScopeError,
    load_source_records,
)

LINE = "=" * 64


def show_plan_view(store, plan_id):
    view = store.plan_view(plan_id)
    print(f"\n【方案视图】{view.plan_title}")
    print("  尚待答复 / 待办：")
    if not view.pending:
        print("    （无）")
    for item in view.pending:
        print(f"    - [{item.kind}] {item.summary}（{item.detail}）")
    print("  部门承诺：")
    if not view.commitments:
        print("    （无）")
    for c in view.commitments:
        status = RESPONSE_STATUS_LABELS[c.status]
        print(f"    - {c.text}（责任单位：{c.unit}，状态：{status}）")
    print("  依据段落：")
    for b in view.basis:
        ref = b.ref
        print(f"    - {b.used_by} 依据 发言{ref.statement_id} 第{ref.version}版第{ref.paragraph}段：{b.text}")


def show_minutes(store, minutes_id):
    minutes = store.get_minutes(minutes_id)
    print(f"\n【听证纪要】{minutes.title}（发布于 {minutes.published_at}）")
    for item in minutes.items:
        who = item.proposer or item.unit
        print(
            f"  [{item.label}] {item.quoted_text}"
            f"（{who}，引用第{item.quoted_version}版，出处：{item.source}）"
        )


def main():
    store = HearingStore(clock=lambda: "2026-11-22")

    print(LINE)
    print("一、登记方案、居民与发言")
    plan_a = store.add_plan("方案甲：滨河步道贴近学校围墙的拟议路线")
    plan_b = store.add_plan("方案乙：绕行绿地的替代路线")
    lin = store.add_speaker("林女士")
    chen = store.add_speaker("陈先生")
    store.add_speaker("市交通局代表", role="agency")

    s1 = store.add_statement(
        lin.id,
        "oral",
        ["支持改善河岸通行，但不赞成方案甲把步道贴近学校围墙。", "建议比较方案乙。"],
    )
    s2 = store.add_statement(chen.id, "written", "我也建议改走方案乙，避开学校。")

    print("\n二、把发言整理为结构化意见（立场＋方案＋提出者＋依据段落）")
    o1 = store.add_opinion(s1.id, lin.id, plan_a.id, "condition",
                           "认可改善目标，但反对步道贴近学校围墙", paragraphs=(1,))
    o2 = store.add_opinion(s1.id, lin.id, plan_b.id, "alternative",
                           "建议比较方案乙绕行绿地路线", paragraphs=(2,))
    o3 = store.add_opinion(s2.id, chen.id, plan_b.id, "alternative",
                           "建议改走方案乙，避开学校", paragraphs=(1,))
    store.mark_related(o2.id, o3.id)
    print(f"  {o1.id}：林女士对方案甲（附条件）；{o2.id}：林女士对方案乙（替代建议）")
    print(f"  {o3.id}：陈先生对方案乙（替代建议）——与 {o2.id} 相似，仅互相参照，不合并")

    print("\n三、登记部门回应（区分解释、待研究、承诺；不得跨方案）")
    r1 = store.add_response(plan_b.id, [o2.id], "deferred",
                            "方案乙的接驳影响带回去研究，下次说明会答复", "市交通局")
    r2 = store.add_response(plan_a.id, [o1.id], "commitment",
                            "承诺夜间十点后停止施工", "市工程处")
    try:
        store.add_response(plan_b.id, [o1.id], "explanation", "并到方案乙一起答复", "市交通局")
    except PlanScopeError as exc:
        print(f"  跨方案答复被拒绝：{exc}")

    print("\n四、发布第一次纪要（“带回去研究”不算正式答复）")
    m1 = store.publish_minutes("第一次听证纪要")
    show_minutes(store, m1.id)

    print("\n五、发言更正与意见撤回：未结束的回应需重新核对")
    store.correct_statement(
        s1.id,
        ["支持改善河岸通行，但反对的是方案甲夜间施工安排。", "建议比较方案乙。"],
        note="发言人更正记录",
    )
    store.correct_opinion(o1.id, "反对的是方案甲夜间施工安排，不反对路线", note="按更正后发言修订")
    print(f"  更正后 {r2.id} 状态：{RESPONSE_STATUS_LABELS[store.responses[r2.id].status]}")
    store.recheck_response(r2.id, "已核对：承诺对象为夜间施工安排，仍然成立")
    store.close_response(r2.id, note="核对后结束")

    store.withdraw_opinion(o3.id, "陈先生书面撤回该项建议")
    print(f"  陈先生撤回 {o3.id}；相似意见 {o2.id} 不受影响，仍独立存在")

    print("\n六、承办人员按方案查看工作视图")
    show_plan_view(store, plan_a.id)
    show_plan_view(store, plan_b.id)

    print("\n七、再次发布纪要；第一次纪要保留当时引用的文本与版本")
    m2 = store.publish_minutes("第二次听证纪要")
    show_minutes(store, m2.id)
    print("\n  第一次纪要仍然引用当时的文本：")
    for item in store.get_minutes(m1.id).opinions():
        print(f"    - {item.ref_id} 第{item.quoted_version}版：{item.quoted_text}")

    print("\n八、从原始发言追到方案争点与后续响应")
    trace = store.statement_trace(s1.id)
    print(f"  发言 {trace.statement_id}（{trace.speaker}，现行第{trace.current_version}版）：")
    for t in trace.opinions:
        print(f"    → {t.opinion.id}（{t.opinion.stance.value}，{t.opinion.status.value}）"
              f" 回应：{'、'.join(t.response_ids) or '（无）'}")

    print("\n九、导入 data/hearings.json 合成原始记录")
    raw_store = HearingStore(clock=lambda: "2026-11-22")
    mapping = load_source_records(Path(__file__).parents[1] / "data" / "hearings.json", raw_store)
    for record_id, statement_id in sorted(mapping.items()):
        st = raw_store.statements[statement_id]
        print(f"  {record_id} → {statement_id}（{st.kind.value}，第{st.current.version}版）")

    out = Path(__file__).parents[1] / "data" / "demo_store.json"
    store.save(out)
    print(f"\n登记簿已保存到 {out}（可用 HearingStore.load 重新载入）")
    print(LINE)


if __name__ == "__main__":
    main()
