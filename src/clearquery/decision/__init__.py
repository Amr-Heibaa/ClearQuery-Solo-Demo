from clearquery.contracts import BIR, Decision

SLOTS = [
    ("metric", "METRIC_UNRESOLVED", "هل تقصد إجمالي المبيعات أم عدد الطلبات؟"),
    ("aggregation_shape", "SHAPE_UNRESOLVED", "هل تريد الإجمالي أم كل فرع أم أعلى فرع؟"),
    ("branch_name", "UNKNOWN_ENTITY", "ما اسم الفرع المقصود؟"),
    ("time", "TIME_UNRESOLVED", "ما الشهر والسنة المقصودان؟"),
]


def decide(bir: BIR):
    if bir.unsupported_reason:
        return Decision(trace_id=bir.trace_id, decision="ABSTAIN", reason_code=bir.unsupported_reason, clarification_question=None)
    missing = set(bir.missing_slots)
    if bir.metric is None: missing.add("metric")
    if bir.aggregation_shape is None: missing.add("aggregation_shape")
    if bir.date_start is None: missing.add("time")
    for slot, reason, question in SLOTS:
        if slot in missing:
            return Decision(trace_id=bir.trace_id, decision="CLARIFY", reason_code=reason, clarification_question=question)
    supported = (bir.entity == "orders" and (
        (bir.metric == "total_sales" and bir.aggregation_shape in {"overall", "by_branch", "top_branch"}) or
        (bir.metric == "order_count" and bir.aggregation_shape == "by_branch")) and
        (bir.branch_name is None or bir.aggregation_shape == "overall"))
    return Decision(trace_id=bir.trace_id, decision="ANSWER" if supported else "ABSTAIN",
        reason_code="INTENT_RESOLVED" if supported else "UNSUPPORTED_INTENT", clarification_question=None)
