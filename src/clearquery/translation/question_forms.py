"""Small complete-question grammar, never a generated-text repair.

Both languages must match a whole supported form before the gate uses these
frames. Unknown verbs, units, constraints, negation and extra clauses therefore
cannot be discarded as harmless vocabulary. Catalog/date slots remain compared
independently. A bare occurrence of 'sell' or 'operate' grants no meaning.
"""
import re
from clearquery.contracts import BIR
from clearquery.intent import CATALOG, MONTHS, normalize

_MONTH = "|".join(re.escape(name) for name in sorted(MONTHS, key=len, reverse=True))
_DATE = rf"(?P<month>{_MONTH}) (?P<year>20[0-9]{{2}})"
_END = r"[?؟.]?"
_BRANCHES = {}
for _row in CATALOG:
    for _alias in (_row["branch_name"], _row["branch_name_ar"]):
        _BRANCHES[normalize(_alias)] = _row["branch_name"]
_BRANCH = "|".join(re.escape(name) for name in sorted(_BRANCHES, key=len, reverse=True))

_AR_MONEY = re.compile(rf"فرع (?P<branch>{_BRANCH}) باع بكام في (?:شهر )?{_DATE}{_END}")
_EN_MONEY = re.compile(rf"how much did (?:the )?(?P<branch>{_BRANCH}) branch sell (?:in|during) {_DATE}{_END}")
_AR_COUNT = re.compile(
    rf"(?:كل فرع (?:عمل|عنده) كام (?:اوردر|اوردرات|طلب|طلبات)|(?:كام )?عدد (?:الطلبات|الاوردرات) لكل فرع"
    rf"|(?:كام )?عدد (?:الطلبات|الاوردرات) اللي (?:تسجلت|اتسجلت) في كل فرع) "
    rf"(?:في|خلال) (?:شهر )?{_DATE}{_END}")
_EN_COUNT = re.compile(
    rf"(?:how many (?:recorded )?orders "
    rf"(?:per branch|did (?:each|every) branch record|were (?:recorded|registered) (?:per|for each|in (?:each|every)) branch|are there (?:per|for each) branch)"
    rf"|what (?:is|was) the (?:recorded )?order count per branch) (?:in|during) {_DATE}{_END}")


def match_question(text: str, trace_id: str, *, english: bool):
    """Return a semantic frame only for a fully consumed, unqualified question.

The money frame relies on the paired Arabic 'باع بكام' money anchor. Quantity
questions ('how many units', 'how much oil'), prices per item, currencies and
business-status filters are outside this grammar. 'Operate' remains unsupported.
"""
    normalized = " ".join(normalize(text).split())
    forms = ((_EN_MONEY, "total_sales", "overall"), (_EN_COUNT, "order_count", "by_branch")) if english else (
        (_AR_MONEY, "total_sales", "overall"), (_AR_COUNT, "order_count", "by_branch"))
    for pattern, metric, shape in forms:
        found = pattern.fullmatch(normalized)
        if found is None:
            continue
        year, month = int(found["year"]), MONTHS[found["month"]]
        branch = found.groupdict().get("branch")
        return BIR(trace_id=trace_id, question_ar=text, entity="orders", metric=metric,
            aggregation_shape=shape, branch_name=_BRANCHES[branch] if branch else None,
            date_start=f"{year:04d}-{month:02d}-01", date_end_exclusive=f"{year + (month == 12):04d}-{month % 12 + 1:02d}-01",
            missing_slots=[], unsupported_reason=None)
    return None
