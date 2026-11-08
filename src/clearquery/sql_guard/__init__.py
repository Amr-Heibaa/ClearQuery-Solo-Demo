"""Fresh pglast guard. Only complete reviewed AST shapes are approved.

Whitespace, optional trailing semicolon, aliases and analytics qualification are
normalized for comparison only. The returned SQL is ALWAYS the original string.
The guard never rewrites a candidate and never executes SQL.
"""
import copy
from pglast import parse_sql, scan
from clearquery.contracts import ValidationResult, Check
from clearquery.decision import decide

STAGES = ["precondition", "parse_single", "comments_statement", "reviewed_shape", "relations_columns", "functions", "bir_consistency"]
COLUMNS = {"orders": {"order_id", "branch_id", "order_date", "total_amount"}, "branches": {"branch_id", "branch_name"}}


def reference_sql(bir):
    """Independent reviewed shapes, parameterized ONLY by resolved server BIR."""
    start, end = bir.date_start, bir.date_end_exclusive
    dates = f"o.order_date >= DATE '{start}' AND o.order_date < DATE '{end}'"
    if bir.aggregation_shape == "by_branch":
        metric = "COUNT(o.order_id) AS order_count" if bir.metric == "order_count" else "COALESCE(SUM(o.total_amount), 0) AS total_sales"
        return f"SELECT b.branch_name, {metric} FROM branches b LEFT JOIN orders o ON o.branch_id = b.branch_id AND {dates} GROUP BY b.branch_id, b.branch_name ORDER BY b.branch_name"
    if bir.aggregation_shape == "top_branch":
        return f"SELECT b.branch_name, SUM(o.total_amount) AS total_sales FROM orders o JOIN branches b ON b.branch_id = o.branch_id WHERE {dates} GROUP BY b.branch_id, b.branch_name ORDER BY total_sales DESC FETCH FIRST 1 ROWS WITH TIES"
    if bir.branch_name:
        # Canonical catalog names are validated by BIR before interpolation.
        return f"SELECT COALESCE(SUM(o.total_amount), 0) AS total_sales FROM orders o JOIN branches b ON b.branch_id = o.branch_id WHERE b.branch_name = '{bir.branch_name}' AND {dates}"
    return f"SELECT COALESCE(SUM(total_amount), 0) AS total_sales FROM orders WHERE order_date >= DATE '{start}' AND order_date < DATE '{end}'"


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from walk(child)


def tag(node):
    return node.get("@")


def string(node):
    return node.get("sval")


def canonical(tree):
    tree = copy.deepcopy(tree)
    relations = [n for n in walk(tree) if tag(n) == "RangeVar"]
    aliases = {}
    tables = set()
    for n in relations:
        table = n["relname"]
        alias = n.get("alias")
        if alias and alias.get("colnames"):
            raise ValueError("Column alias lists are outside reviewed shapes")
        name = alias["aliasname"] if alias else table
        if name in aliases or table in tables:
            raise ValueError("Duplicate relation or alias")
        aliases[name] = table
        tables.add(table)
        n["alias"] = None
        n["schemaname"] = None
    for n in walk(tree):
        if tag(n) != "ColumnRef":
            continue
        fields = n["fields"]
        names = [string(f) for f in fields]
        if not all(names):
            raise ValueError("Projection stars forbidden")
        col = names[-1]
        if len(names) == 1:
            possible = [table for table in tables if col in COLUMNS[table]]
            if len(possible) == 1:
                table = possible[0]
            elif col in {"total_sales", "order_count"}:
                # Only exact reference AST can use aggregate output aliases.
                continue
            else:
                raise ValueError("Unknown or ambiguous column")
        elif len(names) == 2:
            table = aliases.get(names[0])
        elif len(names) == 3 and names[0] == "analytics":
            table = aliases.get(names[1])
        else:
            raise ValueError("Invalid column qualification")
        if table is None or col not in COLUMNS[table]:
            raise ValueError("Column outside approved relation")
        n["fields"] = ({"@": "String", "sval": table}, {"@": "String", "sval": col})
    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if k not in {"location", "stmt_location", "stmt_len"}}
        if isinstance(v, (tuple, list)):
            return tuple(clean(x) for x in v)
        return v
    return clean(tree)


def q4_join_equivalent(tree, bir):
    """Comparison-only equivalence for the reviewed branch-sales INNER JOIN.

Aliases have already been resolved to checked table identities by canonical().
Only orders.branch_id = branches.branch_id may swap, only in this direct join,
and only for branch-filtered overall total_sales. Everything else is compared
unchanged against the complete reference AST. No SQL text is ever modified.
"""
    if not (bir.workspace == "sales" and bir.entity == "orders" and bir.metric == "total_sales"
            and bir.aggregation_shape == "overall" and bir.branch_name is not None):
        return tree
    sources = tree.get("fromClause") or ()
    if len(sources) != 1:
        return tree
    join = sources[0]
    if (tag(join) != "JoinExpr" or join.get("jointype", {}).get("name") != "JOIN_INNER"
            or join.get("isNatural") or join.get("usingClause")):
        return tree
    left, right = join.get("larg", {}), join.get("rarg", {})
    if not (tag(left) == tag(right) == "RangeVar" and left.get("relname") == "orders"
            and right.get("relname") == "branches" and left.get("schemaname") is None
            and right.get("schemaname") is None and left.get("catalogname") is None
            and right.get("catalogname") is None):
        return tree
    equality = join.get("quals") or {}
    if (tag(equality) != "A_Expr" or equality.get("kind", {}).get("name") != "AEXPR_OP"
            or tuple(string(n) for n in equality.get("name", ())) != ("=",)):
        return tree
    def column(node):
        if not isinstance(node, dict) or tag(node) != "ColumnRef":
            return None
        fields = node.get("fields", ())
        if len(fields) != 2 or any(tag(field) != "String" for field in fields):
            return None
        return tuple(string(field) for field in fields)
    if (column(equality.get("lexpr")), column(equality.get("rexpr"))) != (
            ("orders", "branch_id"), ("branches", "branch_id")):
        return tree
    result = copy.deepcopy(tree)
    predicate = result["fromClause"][0]["quals"]
    predicate["lexpr"], predicate["rexpr"] = predicate["rexpr"], predicate["lexpr"]
    return result


class SqlGuard:
    def review(self, sql, bir):
        checks = []
        def reject(code):
            checks.append(Check(name=STAGES[len(checks)], status="FAIL"))
            while len(checks) < len(STAGES):
                checks.append(Check(name=STAGES[len(checks)], status="NOT_RUN"))
            return ValidationResult(trace_id=bir.trace_id, reviewed_sql=sql, validated=False,
                                    rejected_reason=code, checks=checks)
        def passed():
            checks.append(Check(name=STAGES[len(checks)], status="PASS"))
        if decide(bir).decision != "ANSWER":
            return reject("PRECONDITION_FAILED")
        passed()
        try:
            statements = parse_sql(sql)
        except Exception:
            return reject("SQL_PARSE_ERROR")
        if len(statements) != 1:
            return reject("MULTIPLE_STATEMENTS")
        tokens = scan(sql)
        separators = [i for i, token in enumerate(tokens) if token.name == "ASCII_59"]
        meaningful = [i for i, token in enumerate(tokens) if "COMMENT" not in token.name]
        if len(separators) > 1 or (separators and separators[0] != meaningful[-1]):
            return reject("MULTIPLE_STATEMENTS")
        passed()
        # pglast lexer identifies real comments, including nested block comments.
        if any("COMMENT" in token.name for token in tokens):
            return reject("SQL_COMMENTS")
        tree = statements[0].stmt()
        if tag(tree) != "SelectStmt" or tree.get("intoClause") or tree.get("lockingClause"):
            return reject("STATEMENT_NOT_ALLOWED")
        passed()
        if any(tag(n) in {"CommonTableExpr", "WithClause", "SubLink", "RangeSubselect", "RangeFunction", "SQLValueFunction"} for n in walk(tree)):
            return reject("SHAPE_NOT_ALLOWED")
        if tree.get("op", {}).get("name") not in (None, "SETOP_NONE"):
            return reject("SHAPE_NOT_ALLOWED")
        passed()
        for n in walk(tree):
            if tag(n) == "RangeVar" and (n.get("catalogname") is not None or n.get("schemaname") not in (None, "analytics") or n.get("relname") not in COLUMNS):
                return reject("RELATION_NOT_ALLOWED")
        try:
            normalized = canonical(tree)
        except (ValueError, KeyError):
            return reject("COLUMN_NOT_ALLOWED")
        passed()
        for n in walk(tree):
            if tag(n) == "FuncCall":
                fn = [string(x) for x in n["funcname"]]
                if fn not in (["sum"], ["count"]):
                    return reject("FUNCTION_NOT_ALLOWED")
        passed()
        if q4_join_equivalent(normalized, bir) != q4_join_equivalent(canonical(parse_sql(reference_sql(bir))[0].stmt()), bir):
            return reject("BIR_SQL_MISMATCH")
        passed()
        return ValidationResult(trace_id=bir.trace_id, reviewed_sql=sql, validated=True,
                                rejected_reason=None, checks=checks)
