import re


def clean_str(value):
    if value is None:
        return ""
    return str(value).strip()


def build_alias_map(schema: dict):
    alias_map = {}

    for table, meta in schema.items():
        for col in meta.get("columns", []):
            c = col.lower()
            alias_map[c] = col

            if c.endswith("s"):
                alias_map[c[:-1]] = col
            else:
                alias_map[c + "s"] = col

            # common semantic aliases
            if c in {"marks", "mark", "score", "grade", "result"}:
                alias_map["mark"] = col
                alias_map["marks"] = col
                alias_map["score"] = col
                alias_map["grade"] = col
                alias_map["result"] = col

            if c in {"name", "student_name", "employee_name", "customer_name", "patient_name", "doctor_name", "vendor_name", "product_name", "course_name"}:
                alias_map["name"] = col
                alias_map["student_name"] = col
                alias_map["employee_name"] = col
                alias_map["customer_name"] = col
                alias_map["patient_name"] = col
                alias_map["doctor_name"] = col
                alias_map["vendor_name"] = col
                alias_map["product_name"] = col
                alias_map["course_name"] = col

            if c in {"joined_year", "start_year", "created_year", "order_year", "appointment_year", "registered_year", "offered_year"}:
                alias_map["year"] = col
                alias_map["joined"] = col
                alias_map["join_year"] = col
                alias_map["joined_year"] = col
                alias_map["start_year"] = col
                alias_map["created_year"] = col

            if "department" in c:
                alias_map["department"] = col
                alias_map["dept"] = col

    return alias_map


def guess_main_year_column(table: str, schema: dict):
    if table not in schema:
        return None
    cols = schema[table]["columns"]
    preferred = ["joined_year", "start_year", "created_year", "order_year", "appointment_year", "registered_year"]
    for p in preferred:
        if p in cols:
            return p
    for c in cols:
        if "year" in c:
            return c
    return None


def replace_aliases(text: str, alias_map: dict):
    if not text:
        return ""

    # longest aliases first
    for alias, real_col in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        text = re.sub(rf"\b{re.escape(alias)}\b", real_col, text, flags=re.IGNORECASE)

    return text


def repair_fake_join(joins: str, table: str, schema: dict):
    """
    Fix patterns like:
      students JOIN year 2021
      students JOIN 2021
      students JOIN joined year 2021
    into no join, handled as filter.
    """
    joins = clean_str(joins)
    if not joins:
        return "", ""

    low = joins.lower()

    # detect suspicious year join
    year_match = re.search(r"\b(19|20)\d{2}\b", low)
    if "join" in low and year_match:
        year_col = guess_main_year_column(table, schema)
        if year_col:
            year_value = year_match.group(0)
            return "", f"{year_col} = {year_value}"

    # hallucinated fake join keywords
    fake_fragments = [
        "join year",
        "join sql",
        "join mysql",
        "join table",
        "join database",
        "college preparatory",
        "table names",
    ]
    if any(frag in low for frag in fake_fragments):
        return "", ""

    return joins, ""


def repair_missing_operators(filters: str, table: str, schema: dict):
    """
    Fix:
      joined_year 2021  -> joined_year = 2021
      department marketing -> department = 'marketing'
      marks 80 -> marks = 80
    """
    filters = clean_str(filters)
    if not filters:
        return ""

    allowed_cols = schema.get(table, {}).get("columns", [])

    # numeric pattern: col 2021 -> col = 2021
    for col in allowed_cols:
        filters = re.sub(
            rf"\b({re.escape(col)})\s+(\d+(?:\.\d+)?)\b",
            r"\1 = \2",
            filters,
            flags=re.IGNORECASE
        )

    # text pattern: department marketing -> department = 'marketing'
    for col in allowed_cols:
        filters = re.sub(
            rf"\b({re.escape(col)})\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            lambda m: f"{m.group(1)} = '{m.group(2)}'"
            if m.group(2).lower() not in {"and", "or", "like", "between", "in", "is", "null"}
            else m.group(0),
            filters,
            flags=re.IGNORECASE
        )

    return filters


def repair_like_without_quotes(filters: str):
    if not filters:
        return ""

    return re.sub(
        r"(\b[A-Za-z_][A-Za-z0-9_\.]*\b)\s+LIKE\s+([A-Za-z][A-Za-z0-9_]*)",
        r"\1 LIKE '%\2%'",
        filters,
        flags=re.IGNORECASE
    )


def repair_text_equals_without_quotes(filters: str, schema: dict, table: str):
    if not filters:
        return ""

    allowed_cols = set(schema.get(table, {}).get("columns", []))

    def repl(match):
        col = match.group(1)
        op = match.group(2)
        val = match.group(3)

        if col not in allowed_cols:
            return match.group(0)

        if re.fullmatch(r"\d+(\.\d+)?", val):
            return f"{col} {op} {val}"

        if val.startswith("'") and val.endswith("'"):
            return f"{col} {op} {val}"

        return f"{col} {op} '{val}'"

    return re.sub(
        r"\b([A-Za-z_][A-Za-z0-9_\.]*)\b\s*(=|!=|<>|>|<|>=|<=)\s*([A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?)",
        repl,
        filters
    )


def repair_joined_year_style(filters: str, table: str, schema: dict):
    """
    Fix:
      joined year 2021
      joined_year 2021
      join year 2021
    """
    if not filters:
        return ""

    year_col = guess_main_year_column(table, schema)
    if not year_col:
        return filters

    filters = re.sub(r"\bjoined year\b", year_col, filters, flags=re.IGNORECASE)
    filters = re.sub(r"\bjoin year\b", year_col, filters, flags=re.IGNORECASE)
    filters = re.sub(r"\byear joined\b", year_col, filters, flags=re.IGNORECASE)

    filters = re.sub(
        rf"\b{re.escape(year_col)}\b\s+(\d{{4}})\b",
        rf"{year_col} = \1",
        filters,
        flags=re.IGNORECASE
    )

    return filters


def repair_filter_separators(filters: str):
    if not filters:
        return ""

    # convert ; to AND
    filters = filters.replace(";", " AND ")

    # normalize spacing
    filters = re.sub(r"\s+", " ", filters).strip()

    # split conditions
    parts = re.split(r"\s+AND\s+", filters, flags=re.IGNORECASE)

    # remove duplicates
    seen = set()
    unique = []

    for p in parts:
        key = p.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(p.strip())

    return " AND ".join(unique)

def auto_repair_meaning(pred: dict, schema: dict):
    """
    Repairs model output before validation.
    Returns repaired dict.
    """
    pred = dict(pred)

    table = clean_str(pred.get("target_table", ""))
    alias_map = build_alias_map(schema)

    # normalize columns / filters / sort column aliases
    pred["target_columns"] = replace_aliases(clean_str(pred.get("target_columns", "*")), alias_map)
    pred["filters"] = replace_aliases(clean_str(pred.get("filters", "")), alias_map)
    pred["sort_column"] = replace_aliases(clean_str(pred.get("sort_column", "")), alias_map)
    pred["group_by"] = replace_aliases(clean_str(pred.get("group_by", "")), alias_map)
    pred["joins"] = replace_aliases(clean_str(pred.get("joins", "")), alias_map)

    # repair suspicious joins
    repaired_joins, inferred_filter = repair_fake_join(pred["joins"], table, schema)
    pred["joins"] = repaired_joins

    if inferred_filter:
        if pred["filters"]:
            pred["filters"] = f"{pred['filters']} AND {inferred_filter}"
        else:
            pred["filters"] = inferred_filter

    # fix joined year style
    pred["filters"] = repair_joined_year_style(pred["filters"], table, schema)

    # fix missing operators
    pred["filters"] = repair_missing_operators(pred["filters"], table, schema)

    # fix LIKE values
    pred["filters"] = repair_like_without_quotes(pred["filters"])

    # fix text equality quoting
    pred["filters"] = repair_text_equals_without_quotes(pred["filters"], schema, table)

    # cleanup separators
    pred["filters"] = repair_filter_separators(pred["filters"])

    # if join removed and nothing left, convert join_select → select
    if not pred["joins"] and clean_str(pred.get("intent", "")).lower() in {"join_select", "join_aggregate", "multi_join_select"}:
        if pred.get("group_by") or pred.get("aggregate"):
            pred["intent"] = "group_by_aggregate"
        else:
            pred["intent"] = "select"

    return pred