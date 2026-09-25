"""两条提交的并排对比（纯数据计算，不依赖 Flask）。

API 层负责取数与鉴权，本模块负责：
  - validate_pair：校验两条提交是否可以对比（同一道题）；
  - build_comparison：汇总判定结果、得分、耗时、内存差异，
    选出每项指标更优的一侧，并按测试点对齐两侧明细。

约定：
  - 数值型指标返回 {"a": v, "b": v, "winner": "a"|"b"|None,
    "delta": a-b, "comparable": bool}；
  - 编译失败（CE）或仍在评测中的提交没有有效运行数据，
    不参与“更快 / 更省内存”的评选，避免把 0 ms 误判成最快。
"""

# 尚未产出有效运行数据的状态
_PENDING_STATES = {"PENDING", "JUDGING"}
_NO_RUN_STATES = _PENDING_STATES | {"CE"}


def state_of(sub):
    """提交所处阶段：pending（评测中）/ compile_error / done。"""
    status = (sub or {}).get("status") or ""
    if status in _PENDING_STATES:
        return "pending"
    if status == "CE":
        return "compile_error"
    return "done"


def validate_pair(sub_a, sub_b):
    """返回无法对比时的中文提示；可以对比时返回 None。"""
    if sub_a is None or sub_b is None:
        return "提交不存在或已被删除"
    pa, pb = sub_a.get("problem_id"), sub_b.get("problem_id")
    if pa != pb:
        return (f"两条提交不属于同一道题"
                f"（{pa or '未知题目'} 与 {pb or '未知题目'}），无法对比")
    return None


def _number(value):
    """尽量转成浮点数；无法转换返回 None。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _numeric_metric(sub_a, sub_b, key, lower_better, require_done=True):
    """比较单个数值型指标。

    lower_better=True 时更小更优（耗时、内存），否则更大更优（得分）。
    require_done=True 时两侧都必须真正运行过（CE/评测中不参评）。
    """
    va, vb = _number(sub_a.get(key)), _number(sub_b.get(key))
    comparable = va is not None and vb is not None
    if require_done:
        comparable = (comparable
                      and state_of(sub_a) == "done"
                      and state_of(sub_b) == "done")
    entry = {
        "a": sub_a.get(key), "b": sub_b.get(key),
        "winner": None, "delta": None, "comparable": comparable,
    }
    if not comparable or va == vb:
        return entry
    a_better = va < vb if lower_better else va > vb
    entry["winner"] = "a" if a_better else "b"
    entry["delta"] = va - vb
    return entry


def _categorical_metric(sub_a, sub_b, key):
    va, vb = sub_a.get(key), sub_b.get(key)
    return {"a": va, "b": vb, "diff": va != vb}


def _case_winner(detail_a, detail_b, key, lower_better):
    """单个测试点上的数值更优侧；仅两侧均为 AC 时才评选。"""
    if not detail_a or not detail_b:
        return None
    if detail_a.get("status") != "AC" or detail_b.get("status") != "AC":
        return None
    va, vb = _number(detail_a.get(key)), _number(detail_b.get(key))
    if va is None or vb is None or va == vb:
        return None
    a_better = va < vb if lower_better else va > vb
    return "a" if a_better else "b"


def _case_rows(sub_a, sub_b):
    """按 case_id 对齐两侧测试点明细（保留 A 侧顺序，再补 B 侧多出的）。"""
    details_a = sub_a.get("details") or []
    details_b = sub_b.get("details") or []
    map_a = {str(c.get("case_id")): c for c in details_a}
    map_b = {str(c.get("case_id")): c for c in details_b}

    ordered_ids = [str(c.get("case_id")) for c in details_a]
    for c in details_b:
        cid = str(c.get("case_id"))
        if cid not in map_a:
            ordered_ids.append(cid)

    rows = []
    for cid in ordered_ids:
        da, db = map_a.get(cid), map_b.get(cid)
        rows.append({
            "case_id": cid,
            "a": da,
            "b": db,
            "status_diff": (da or {}).get("status") != (db or {}).get("status"),
            "time_winner": _case_winner(da, db, "time_ms", lower_better=True),
            "memory_winner": _case_winner(da, db, "memory_kb", lower_better=True),
        })
    return rows


def build_comparison(sub_a, sub_b):
    """组装两条提交的完整对比数据（调用前应先通过 validate_pair）。"""
    msg_a = sub_a.get("compile_message") or ""
    msg_b = sub_b.get("compile_message") or ""
    metrics = {
        "status": _categorical_metric(sub_a, sub_b, "status"),
        "language": _categorical_metric(sub_a, sub_b, "language"),
        "created_at": _categorical_metric(sub_a, sub_b, "created_at"),
        # 得分：CE/未评完也有 0 分，始终参与比较
        "score": _numeric_metric(sub_a, sub_b, "score",
                                 lower_better=False, require_done=False),
        # 耗时/内存：只有真正跑过测试点才有效
        "time_ms": _numeric_metric(sub_a, sub_b, "time_ms", lower_better=True),
        "memory_kb": _numeric_metric(sub_a, sub_b, "memory_kb", lower_better=True),
    }
    return {
        "problem_id": sub_a.get("problem_id"),
        "state": {"a": state_of(sub_a), "b": state_of(sub_b)},
        "metrics": metrics,
        "cases": _case_rows(sub_a, sub_b),
        "compile": {
            "a": msg_a, "b": msg_b,
            "diff": msg_a != msg_b, "show": bool(msg_a or msg_b),
        },
        "a": sub_a,
        "b": sub_b,
    }
