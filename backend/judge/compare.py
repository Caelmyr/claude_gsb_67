"""两条提交的并排对比逻辑。

对比仅在同一道题的两条提交之间进行，比对维度包括：
判定结果、得分、耗时、内存、语言、逐测试点明细、编译信息。

为便于前端「标出不一样的地方」，这里同时返回：
  - a / b        两侧各自的规范化数据；
  - verdict      总体判定对比（same / better / worse / both_fail）；
  - metrics      各数值指标（score/time_ms/memory_kb）的差值与更优侧；
  - cases        按 case_id 对齐后的逐测试点对比；
  - diff_fields  存在差异的字段名集合。

所有数值差值均按「a 相对 b」给出：
  delta > 0 表示 a 更大；对耗时/内存而言更小才更优，
  因此 better 侧由 winner 字段单独标明，避免前端自行猜测方向。
"""

# 判定越靠前越优（PENDING/JUDGING 视为尚未出结果，不参与优劣排序）
VERDICT_RANK = {
    "AC": 0,
    "TLE": 1, "MLE": 2, "OLE": 3,
    "WA": 4, "RE": 5, "CE": 6, "SE": 7,
}
PENDING_STATUSES = ("PENDING", "JUDGING", "")


def _rank(status):
    return VERDICT_RANK.get(status, 8)


def _is_pending(status):
    return (status or "") in PENDING_STATUSES


def _has_perf(status):
    """该判定下是否存在可信的耗时/内存测量值。

    CE 没有运行过程、PENDING/JUDGING 尚未运行，其 0 只是占位值，
    若参与数值对比会把「编译失败」误判成「更快、更省内存」。
    """
    return not _is_pending(status) and status != "CE"


def _num(value):
    """容错地把数值字段转成数字；缺失/非法返回 None。"""
    if value is None or value is False:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric(a, b, key, lower_is_better):
    """对比单个数值指标。

    返回 {"values": (x, y), "delta": a-b, "winner": "a"/"b"/"tie"/None}。
    任一侧没有有效数值（例如编译失败没有耗时）时 winner 为 None。
    """
    x, y = _num(a.get(key)), _num(b.get(key))
    out = {"values": (x, y), "delta": None, "winner": None}
    if x is None or y is None:
        return out
    out["delta"] = round(x - y, 6)
    if x == y:
        out["winner"] = "tie"
    else:
        a_better = (x < y) if lower_is_better else (x > y)
        out["winner"] = "a" if a_better else "b"
    return out


def _verdict_cmp(a_status, b_status):
    """总体判定对比。

    返回 (relation, pending)：
      same       判定相同（含同为 PENDING）
      better     a 判定更优
      worse      a 判定更差
      both_fail  两侧都没通过且无法据此分优劣（同为失败态且排名相同）
      pending    至少一侧尚未评测完成
    """
    if _is_pending(a_status) or _is_pending(b_status):
        return "pending", True
    if a_status == b_status:
        return "same", False
    ra, rb = _rank(a_status), _rank(b_status)
    if ra == rb:
        return "both_fail", False
    return ("better" if ra < rb else "worse"), False


def _case_map(sub):
    """把 details 列表转成 case_id -> detail，便于对齐。"""
    out = {}
    for d in sub.get("details") or []:
        cid = d.get("case_id")
        if cid is not None and cid not in out:
            out[cid] = d
    return out


def _compare_cases(a_sub, b_sub):
    """按 case_id 对齐逐测试点对比。"""
    ca, cb = _case_map(a_sub), _case_map(b_sub)
    case_ids = list(ca.keys())
    # 保留 a 的顺序，再追加 b 独有的
    for cid in cb:
        if cid not in case_ids:
            case_ids.append(cid)

    rows = []
    diff_case_count = 0
    for cid in case_ids:
        da, db = ca.get(cid), cb.get(cid)
        sa = (da or {}).get("status")
        sb = (db or {}).get("status")
        relation, _ = _verdict_cmp(sa or "SE", sb or "SE")
        # 某一侧缺失该测试点单独标记
        if da is None or db is None:
            relation = "missing"
        time_cmp = _metric(da or {}, db or {}, "time_ms", lower_is_better=True)
        mem_cmp = _metric(da or {}, db or {}, "memory_kb", lower_is_better=True)
        score_cmp = _metric(da or {}, db or {}, "score", lower_is_better=False)
        differs = (da is None or db is None
                   or relation != "same"
                   or time_cmp["winner"] not in (None, "tie")
                   or mem_cmp["winner"] not in (None, "tie")
                   or score_cmp["winner"] not in (None, "tie"))
        if differs:
            diff_case_count += 1
        rows.append({
            "case_id": cid,
            "a": da or None,
            "b": db or None,
            "relation": relation,
            "time": time_cmp,
            "memory": mem_cmp,
            "score": score_cmp,
            "differs": differs,
        })
    return rows, diff_case_count


def compare_submissions(a_sub, b_sub):
    """对同一道题的两条提交生成完整对比结果。

    调用方需自行保证两条提交存在、属于同一 problem_id、且当前用户有权查看。
    """
    a_status = a_sub.get("status") or "PENDING"
    b_status = b_sub.get("status") or "PENDING"
    verdict_relation, pending = _verdict_cmp(a_status, b_status)

    # CE / 未完成侧的 0 只是占位，不参与耗时/内存对比
    a_perf = a_sub if _has_perf(a_status) else {**a_sub, "time_ms": None, "memory_kb": None}
    b_perf = b_sub if _has_perf(b_status) else {**b_sub, "time_ms": None, "memory_kb": None}
    metrics = {
        "score": _metric(a_sub, b_sub, "score", lower_is_better=False),
        "time_ms": _metric(a_perf, b_perf, "time_ms", lower_is_better=True),
        "memory_kb": _metric(a_perf, b_perf, "memory_kb", lower_is_better=True),
    }
    case_rows, diff_case_count = _compare_cases(a_sub, b_sub)

    diff_fields = set()
    if verdict_relation not in ("same",):
        diff_fields.add("status")
    if metrics["score"]["winner"] not in (None, "tie"):
        diff_fields.add("score")
    if metrics["time_ms"]["winner"] not in (None, "tie"):
        diff_fields.add("time_ms")
    if metrics["memory_kb"]["winner"] not in (None, "tie"):
        diff_fields.add("memory_kb")
    if (a_sub.get("language") or "") != (b_sub.get("language") or ""):
        diff_fields.add("language")
    if (a_sub.get("compile_message") or "") != (b_sub.get("compile_message") or ""):
        diff_fields.add("compile_message")
    if diff_case_count:
        diff_fields.add("details")

    def _side(sub, perf, status):
        return {
            "id": sub.get("id"),
            "problem_id": sub.get("problem_id"),
            "language": sub.get("language") or "",
            "status": status,
            "score": _num(sub.get("score")),
            "time_ms": _num(perf.get("time_ms")),
            "memory_kb": _num(perf.get("memory_kb")),
            "created_at": sub.get("created_at") or "",
            "judged_at": sub.get("judged_at") or "",
            "username": sub.get("username") or "",
            "nickname": sub.get("nickname") or "",
            "compile_message": sub.get("compile_message") or "",
            "pending": _is_pending(status),
        }

    return {
        "problem_id": a_sub.get("problem_id"),
        "a": _side(a_sub, a_perf, a_status),
        "b": _side(b_sub, b_perf, b_status),
        "verdict": {"relation": verdict_relation, "pending": pending},
        "metrics": metrics,
        "cases": case_rows,
        "case_total": len(case_rows),
        "case_diffs": diff_case_count,
        "diff_fields": sorted(diff_fields),
        "identical": not diff_fields and verdict_relation == "same",
    }
