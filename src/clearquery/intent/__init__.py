"""Deterministic bounded Sales grammar; unknown wording cannot pass the semantic gate."""
import calendar
import json
import re
from pathlib import Path
from clearquery.contracts import BIR

ROOT = Path(__file__).resolve().parents[3]
CATALOG = json.loads((ROOT / "data/branch_catalog.json").read_text(encoding="utf-8"))["branches"]


def normalize(text):
    text = text.lower().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩أإآى", "0123456789اااي"))
    return re.sub(r"[\u064b-\u065fـ]", "", text)


MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "مايو": 5, "يونيو": 6,
    "يوليو": 7, "اغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    **{calendar.month_name[i].lower(): i for i in range(1, 13)},
}


def parse(question, trace_id, *, english=False):
    t = normalize(question)
    remaining = t
    found = []
    for branch in CATALOG:
        aliases = [branch["branch_name"], branch["branch_name_ar"]]
        if branch["branch_name"] == "Alexandria":
            aliases += ["اسكندرية", "الاسكندرية"]
        if branch["branch_name"] == "Maadi":
            aliases += ["معادي"]
        if any(normalize(a) in t for a in aliases):
            found.append(branch["branch_name"])
        for a in sorted(aliases, key=len, reverse=True):
            remaining = remaining.replace(normalize(a), " ")
    branch_name = found[0] if len(found) == 1 else None
    months = {v for k, v in MONTHS.items() if re.search(r"(?<!\w)" + k + r"(?!\w)", t)}
    years = re.findall(r"(?<!\d)(20\d{2})(?!\d)", t)
    start = end = None
    if len(months) == 1 and len(years) == 1:
        m, y = next(iter(months)), int(years[0])
        start = f"{y:04d}-{m:02d}-01"
        end = f"{y + (m == 12):04d}-{m % 12 + 1:02d}-01"
    # Reference date is fixed by G0, not host time.
    if any(s in t for s in ["الشهر اللي فات", "الشهر الماضي", "last month"]) and not months and not years:
        start, end = "2026-10-01", "2026-11-01"
    metric = None
    if re.search(r"مبيعات|باع|sales", t):
        metric = "total_sales"
    count = bool(re.search(r"اوردر|طلبات|orders?", t))
    conflict = metric is not None and count
    if count and not conflict:
        metric = "order_count"
    top = bool(re.search(r"اعلي|اكتر|highest|top", t))
    best = bool(re.search(r"افضل|احسن|best", t))
    by_branch = bool(re.search(r"لكل فرع|كل فرع|حسب الفرع|per branch|each branch|by branch", t))
    all_branches = bool(re.search(r"كل الفروع|جميع الفروع|all branches|overall", t))
    shape = "top_branch" if top else "by_branch" if by_branch else "overall"
    if best and not top:
        metric, shape = None, None
    missing = []
    if metric is None or conflict:
        missing.append("metric")
    if shape is None:
        missing.append("aggregation_shape")
    # An unspecified named branch is different from an explicitly global query.
    if len(found) > 1 or (re.search(r"فرع|branch", t) and not (found or by_branch or all_branches or top or best)):
        missing.append("branch_name")
    if not start:
        missing.append("time")
    reason = None
    if re.search(r"احذف|امسح|عدل|اضف|delete|drop|insert|update|truncate|create|grant", t):
        reason = "WRITE_REQUEST"
    elif re.search(r"ربح|ارباح|مرتجع|وحدات|صافي|profit|returns|units|net sales", t):
        reason = "MISSING_DATA"
    elif re.search(r"موظف|رواتب|employee|salary|payroll", t):
        reason = "OUT_OF_WORKSPACE"
    elif metric == "order_count" and shape != "by_branch":
        reason = "UNSUPPORTED_INTENT"
    if branch_name and shape != "overall":
        reason = reason or "UNSUPPORTED_INTENT"
        branch_name = None
    # Coverage is deliberately conservative: numerical thresholds, negation,
    # extra entities, currencies or date qualifiers are NOT discarded.
    for name in MONTHS:
        remaining = re.sub(r"(?<!\w)" + name + r"(?!\w)", " ", remaining)
    remaining = re.sub(r"(?<!\d)20\d{2}(?!\d)", " ", remaining)
    vocabulary = normalize("اجمالي المبيعات مبيعات المسجلة مسجلة المسجله مسجله حقق لكل فرع في كام كل عمل اوردر اوردرات طلب طلبات عدد الطلبات الاوردرات الفروع جميع حسب الفرع باع بكام انهي اني اي عنده اعلي اكتر احسن افضل شهر خلال من هو ما هي قد ايه الشهر اللي فات الماضي لسنة سنة "
        "total recorded sales per branch in how much each orders order count number of all branches overall what is the for which has highest top best achieved during month last show me across")
    allowed = set(vocabulary.split())
    words = re.findall(r"[\w]+", remaining)
    covered = all(w in allowed for w in words) and not conflict and len(found) <= 1
    # Reject symbols beyond harmless question punctuation; e.g. comparisons and SQL.
    covered = covered and not re.search(r"[^\w\s؟?,.،]", remaining)
    bir = BIR(trace_id=trace_id, question_ar=question, entity="orders", metric=metric,
        aggregation_shape=shape, branch_name=branch_name, date_start=start,
        date_end_exclusive=end, missing_slots=missing, unsupported_reason=reason)
    return bir, covered
