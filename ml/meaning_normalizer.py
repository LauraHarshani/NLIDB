import json
import re


def clean_str(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_load_json(raw):
    """
    Parse model output robustly.
    Handles:
    - direct dict
    - json string
    - escaped json string
    """
    if isinstance(raw, dict):
        return raw

    text = clean_str(raw)
    if not text:
        return {}

    # direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # try unicode unescape
    try:
        text2 = bytes(text, "utf-8").decode("unicode_escape")
        return json.loads(text2)
    except Exception:
        pass

    # strip leading/trailing quotes if model wrapped whole json as string
    if text.startswith('"') and text.endswith('"'):
        try:
            text3 = text[1:-1].replace('\\"', '"')
            return json.loads(text3)
        except Exception:
            pass

    return {"raw_output": text, "parse_error": True}


def normalize_intent(intent: str) -> str:
    intent = clean_str(intent).lower()

    mapping = {
        "select": "select",
        "count": "count",
        "aggregate": "aggregate",
        "group_by": "group_by",
        "group_by_aggregate": "group_by_aggregate",
        "join_select": "join_select",
        "join_aggregate": "join_aggregate",
        "multi_join_select": "multi_join_select",
        "nested_select": "nested_select",
    }
    return mapping.get(intent, "select")


def normalize_sort_order(order: str) -> str:
    order = clean_str(order).lower()
    if order in {"desc", "descending"}:
        return "DESC"
    if order in {"asc", "ascending"}:
        return "ASC"
    return ""


def normalize_aggregate(aggregate: str) -> str:
    aggregate = clean_str(aggregate).lower()
    mapping = {
        "count": "count",
        "count_distinct": "count_distinct",
        "avg": "avg",
        "average": "avg",
        "sum": "sum",
        "max": "max",
        "min": "min",
    }
    return mapping.get(aggregate, "")


def normalize_limit(limit_value):
    limit_value = clean_str(limit_value)
    if not limit_value:
        return ""
    try:
        # sometimes model returns "3.0"
        return str(int(float(limit_value)))
    except Exception:
        return ""


def build_alias_map(schema: dict):
    """
    Create a lightweight alias map from schema for common NL variations.
    """
    alias_map = {}

    for table, meta in schema.items():
        cols = meta.get("columns", [])
        for col in cols:
            c = col.lower()

            # exact
            alias_map[c] = col

            # singular/plural heuristics
            if c.endswith("s"):
                alias_map[c[:-1]] = col
            else:
                alias_map[c + "s"] = col

            # common manual aliases
            if c in {"marks", "mark", "score", "grade", "result"}:
                alias_map["mark"] = col
                alias_map["marks"] = col
                alias_map["score"] = col
                alias_map["grade"] = col
                alias_map["result"] = col

            if c in {"student_name", "employee_name", "customer_name", "patient_name", "doctor_name", "vendor_name", "product_name", "course_name", "department_name", "project_name"}:
                alias_map["name"] = col

            if c in {"joined_year", "start_year", "created_year", "order_year", "appointment_year", "registered_year", "offered_year"}:
                alias_map["year"] = col
                if "joined" in c:
                    alias_map["joined"] = col
                if "start" in c:
                    alias_map["start"] = col

            if "department" in c:
                alias_map["department"] = col
                alias_map["dept"] = col

    return alias_map


def normalize_columns(target_columns: str, schema: dict, table: str):
    target_columns = clean_str(target_columns)
    if not target_columns or target_columns == "*":
        return "*"

    alias_map = build_alias_map(schema)
    parts = [p.strip() for p in target_columns.split(",") if p.strip()]
    normalized = []

    for p in parts:
        key = p.lower().strip()
        normalized.append(alias_map.get(key, p))

    return ",".join(normalized)


def normalize_filter_text(filters: str, schema: dict, table: str):
    """
    Normalize text filters:
    - mark -> marks
    - score -> marks (if mapped)
    - LIKE Kasun -> LIKE '%Kasun%'
    - department = marketing -> department = 'marketing'
    - joined year -> joined_year
    """
    filters = clean_str(filters)
    if not filters:
        return ""

    alias_map = build_alias_map(schema)

    # replace multi-word aliases first
    replacements = [
        ("joined year", alias_map.get("joined", "joined_year")),
        ("start year", alias_map.get("start", "start_year")),
        ("student name", alias_map.get("name", "name")),
        ("employee name", alias_map.get("name", "name")),
        ("product name", alias_map.get("name", "name")),
    ]
    low_filters = filters.lower()
    for old, new in replacements:
        low_filters = low_filters.replace(old, new.lower())

    filters = low_filters

    # replace single-word aliases by token boundary
    for alias, real_col in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        filters = re.sub(rf"\b{re.escape(alias.lower())}\b", real_col, filters)

    # normalize operators
    filters = filters.replace("==", "=")

    # LIKE value without quotes → LIKE '%value%'
    filters = re.sub(
        r"(\b[A-Za-z_][A-Za-z0-9_\.]*\b)\s+like\s+([A-Za-z][A-Za-z0-9_]*)",
        r"\1 LIKE '%\2%'",
        filters,
        flags=re.IGNORECASE
    )

    # = value without quotes for plain text tokens
    def quote_text_values(match):
        col = match.group(1)
        op = match.group(2)
        val = match.group(3)

        # don't quote numbers
        if re.fullmatch(r"\d+(\.\d+)?", val):
            return f"{col} {op} {val}"

        # already quoted
        if val.startswith("'") and val.endswith("'"):
            return f"{col} {op} {val}"

        return f"{col} {op} '{val}'"

    filters = re.sub(
        r"(\b[A-Za-z_][A-Za-z0-9_\.]*\b)\s*(=|!=|<>|>|<|>=|<=)\s*([A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?)",
        quote_text_values,
        filters
    )

    # convert semicolon separators to AND
    filters = filters.replace(";", " AND ")

    # collapse spaces
    filters = re.sub(r"\s+", " ", filters).strip()

    return filters


def normalize_group_by(group_by: str, schema: dict, table: str):
    group_by = clean_str(group_by)
    if not group_by:
        return ""

    alias_map = build_alias_map(schema)
    cols = [c.strip() for c in group_by.split(",") if c.strip()]
    normalized = []
    for c in cols:
        normalized.append(alias_map.get(c.lower(), c))
    return ",".join(normalized)


def normalize_sort_column(sort_column: str, schema: dict, table: str):
    sort_column = clean_str(sort_column)
    if not sort_column:
        return ""

    alias_map = build_alias_map(schema)
    return alias_map.get(sort_column.lower(), sort_column)


def normalize_joins(joins: str, schema: dict):
    joins = clean_str(joins)
    if not joins:
        return ""

    # remove obviously fake or hallucinated phrases
    bad_phrases = [
        "college preparatory",
        "sql",
        "mysql",
        "table names",
        "database schema",
    ]
    low = joins.lower()
    for phrase in bad_phrases:
        if phrase in low:
            return ""

    return joins


def normalize_meaning(pred: dict, schema: dict):
    """
    Main normalization entrypoint.
    """
    pred = safe_load_json(pred)

    if pred.get("parse_error"):
        return pred

    table = clean_str(pred.get("target_table", ""))

    normalized = {
        "intent": normalize_intent(pred.get("intent", "")),
        "target_table": table,
        "target_columns": normalize_columns(pred.get("target_columns", "*"), schema, table) if table else clean_str(pred.get("target_columns", "*")),
        "filters": normalize_filter_text(pred.get("filters", ""), schema, table) if table else clean_str(pred.get("filters", "")),
        "joins": normalize_joins(pred.get("joins", ""), schema),
        "group_by": normalize_group_by(pred.get("group_by", ""), schema, table) if table else clean_str(pred.get("group_by", "")),
        "sort_column": normalize_sort_column(pred.get("sort_column", ""), schema, table) if table else clean_str(pred.get("sort_column", "")),
        "sort_order": normalize_sort_order(pred.get("sort_order", "")),
        "limit_value": normalize_limit(pred.get("limit_value", "")),
        "aggregate": normalize_aggregate(pred.get("aggregate", "")),
    }

    # Intent correction rules
    if normalized["joins"]:
        if normalized["aggregate"] or normalized["group_by"]:
            normalized["intent"] = "join_aggregate"
        else:
            normalized["intent"] = "join_select"

    if normalized["group_by"] and normalized["aggregate"]:
        normalized["intent"] = "group_by_aggregate"

    if normalized["aggregate"] == "count":
        normalized["intent"] = "count"

    return normalized