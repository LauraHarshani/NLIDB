from rapidfuzz import fuzz
import re

from .schema_dynamic import normalize

STOPWORDS = {
    "find","show","list","get","give","display","all","the","a","an","of","for","to","with",
    "who","whose","which","that","in","on","at","from","by","and","or",
    "student","students","employee","employees",
    "mark","marks","score","scores","grade","result","results",
    "salary","department","joined","join","joined_year","start_year","year",
    "top","highest","lowest","best","worst","above","over","greater","less","below","under",
    "after","before","count","number"
}

def _pick_similar_column(schema_cols: list[str], candidates: list[str]) -> str | None:
    best = None
    best_score = 0
    for c in schema_cols:
        cn = normalize(c)
        for cand in candidates:
            s = fuzz.partial_ratio(cand, cn)
            if s > best_score:
                best_score = s
                best = c
    return best if best_score >= 85 else None

def _extract_top_n(q: str) -> int | None:
    m = re.search(r"\btop\s+(\d+)\b", q.lower())
    return int(m.group(1)) if m else None

def _extract_year_after_before(q: str):
    ql = q.lower()
    m_after = re.search(r"\b(after|since)\s+(\d{4})\b", ql)
    if m_after:
        return (">", int(m_after.group(2)))
    m_before = re.search(r"\b(before)\s+(\d{4})\b", ql)
    if m_before:
        return ("<", int(m_before.group(2)))
    return (None, None)

def _extract_threshold(q: str):
    ql = q.lower()
    m = re.search(r"\b(marks?|score|salary)\b.*?\b(above|over|greater than|more than)\s+(\d+)\b", ql)
    if m:
        return (m.group(1), ">", int(m.group(3)))
    m2 = re.search(r"\b(marks?|score|salary)\b.*?\b(below|under|less than)\s+(\d+)\b", ql)
    if m2:
        return (m2.group(1), "<", int(m2.group(3)))
    m3 = re.search(r"\b(above|over|greater than|more than)\s+(\d+)\b.*?\b(marks?|score|salary)\b", ql)
    if m3:
        return (m3.group(3), ">", int(m3.group(2)))
    return (None, None, None)

def _extract_department(q: str, schema: dict, table: str):
    ql = q.lower()
    dep_col = _pick_similar_column(schema[table]["columns"], ["department", "dept"])
    if not dep_col:
        return (None, None)

    m = re.search(r"\b(in|from)\s+([a-zA-Z]{2,30})\b", ql)
    if m:
        val = m.group(2).strip()
        if val not in STOPWORDS:
            return (dep_col, val)

    m2 = re.search(r"\bdepartment\s+([a-zA-Z]{2,30})\b", ql)
    if m2:
        val = m2.group(1).strip()
        if val not in STOPWORDS:
            return (dep_col, val)

    return (None, None)

def _extract_name(q: str):
    tokens = re.findall(r"[A-Za-z]+", q)
    caps = [t for t in tokens if t[:1].isupper() and t.lower() not in STOPWORDS and len(t) >= 3]
    if caps:
        return caps[0]
    for t in tokens:
        if t.lower() not in STOPWORDS and len(t) >= 3:
            return t
    return None

def _best_year_column(schema: dict, table: str):
    cols = schema[table]["columns"]
    for pref in ["joined_year", "start_year", "year"]:
        c = _pick_similar_column(cols, [pref])
        if c:
            return c
    return None

def _choose_table(q: str, schema: dict) -> str | None:
    ql = q.lower()

    # explicit mentions
    if "students" in ql and "students" in schema:
        return "students"
    if "employees" in ql and "employees" in schema:
        return "employees"

    # keyword-based fallback: compare query words to column names
    best_t = None
    best_score = 0
    for t, meta in schema.items():
        cols = " ".join(meta.get("columns", []))
        score = fuzz.token_set_ratio(ql, cols.lower())
        if score > best_score:
            best_score = score
            best_t = t

    return best_t if best_score >= 30 else None

def nl_to_sql(nl_query: str, schema: dict):
    q = (nl_query or "").strip()
    if not q:
        return {"ok": False, "error": "Empty query"}

    ql = q.lower()
    table = _choose_table(q, schema)

    if not table:
        return {"ok": False, "error": "Could not detect target table. Try mentioning 'students' or 'employees'."}

    cols = "*"
    where = []
    params = {}

    is_count = any(w in ql for w in ["how many", "count", "number of", "total"])
    top_n = _extract_top_n(q)
    wants_top = top_n is not None or any(w in ql for w in ["highest", "top"])

    metric_col = None
    if any(w in ql for w in ["mark", "marks", "score", "grade", "result"]):
        metric_col = _pick_similar_column(schema[table]["columns"], ["marks", "mark", "score", "grade"])
    if "salary" in ql:
        metric_col = _pick_similar_column(schema[table]["columns"], ["salary"])

    if metric_col and "name" in schema[table]["columns"]:
        cols = f"name, {metric_col}"

    metric_word, op, val = _extract_threshold(q)
    if metric_word and op and val is not None:
        if "salary" in metric_word:
            c = _pick_similar_column(schema[table]["columns"], ["salary"])
        else:
            c = _pick_similar_column(schema[table]["columns"], ["marks", "score", "grade"])
        if c:
            where.append(f"{c} {op} :thr")
            params["thr"] = val

    yop, yval = _extract_year_after_before(q)
    if yop and yval:
        yc = _best_year_column(schema, table)
        if yc:
            where.append(f"{yc} {yop} :yearv")
            params["yearv"] = yval

    dep_col, dep_val = _extract_department(q, schema, table)
    if dep_col and dep_val:
        where.append(f"{dep_col} = :dep")
        params["dep"] = dep_val

    looks_like_person = ("find" in ql) or ("named" in ql) or any(tok[:1].isupper() for tok in re.findall(r"[A-Za-z]+", q))
    if looks_like_person and schema[table].get("text_cols"):
        name_val = _extract_name(q)
        if name_val:
            text_col = "name" if "name" in schema[table]["text_cols"] else schema[table]["text_cols"][0]
            where.append(f"{text_col} LIKE :name")
            params["name"] = f"%{name_val}%"

    if is_count:
        cols = "COUNT(*) AS total"

    sql = f"SELECT {cols} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)

    if wants_top and not is_count:
        order_col = _pick_similar_column(schema[table]["columns"], ["marks", "score", "salary"])
        if order_col:
            sql += f" ORDER BY {order_col} DESC"
            sql += f" LIMIT {top_n or 5}"

    return {
        "ok": True,
        "mode": "direct",
        "table": table,
        "sql": sql,
        "params": params,
        "explanation": f"rule_engine: table={table}, count={is_count}, top={bool(wants_top)}, where={len(where)}"
    }