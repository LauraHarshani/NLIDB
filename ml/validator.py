import re


def clean_str(value):
    if value is None:
        return ""
    return str(value).strip()


def split_csv_like(value: str):
    value = clean_str(value)
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def extract_identifiers(text: str):
    """
    Extract candidate SQL identifiers from text.
    """
    if not text:
        return []
    return re.findall(r"[A-Za-z_][A-Za-z0-9_\.]*", text)


def extract_column_like_tokens(filters: str):
    """
    Heuristic extraction of possible column names from filter strings.
    """
    if not filters:
        return []

    tokens = extract_identifiers(filters)
    keywords = {
        "and", "or", "like", "between", "in", "is", "null",
        "select", "from", "where", "avg", "max", "min", "sum", "count",
        "asc", "desc"
    }
    return [t for t in tokens if t.lower() not in keywords]


def validate_table(table: str, schema: dict):
    if not table:
        return False, "Missing target_table"
    if table not in schema:
        return False, f"Unknown table: {table}"
    return True, "ok"


def validate_columns(table: str, target_columns: str, schema: dict):
    if not table or table not in schema:
        return False, "Invalid table for column validation"

    if not target_columns or target_columns == "*":
        return True, "ok"

    allowed = set(schema[table]["columns"])
    for col in split_csv_like(target_columns):
        if col not in allowed:
            return False, f"Unknown target column '{col}' for table '{table}'"
    return True, "ok"


def validate_group_by(table: str, group_by: str, schema: dict):
    if not group_by:
        return True, "ok"

    allowed = set(schema[table]["columns"])
    for col in split_csv_like(group_by):
        if col not in allowed:
            return False, f"Unknown group_by column '{col}' for table '{table}'"
    return True, "ok"


def validate_sort_column(table: str, sort_column: str, schema: dict):
    if not sort_column:
        return True, "ok"
    allowed = set(schema[table]["columns"])
    # also allow computed aliases like avg_value or total_value for grouped queries
    computed_allowed = {"avg_value", "total_value", "max_value", "min_value", "total", "total_distinct", "related_count"}
    if sort_column not in allowed and sort_column not in computed_allowed:
        return False, f"Unknown sort_column '{sort_column}' for table '{table}'"
    return True, "ok"


def validate_limit(limit_value: str):
    if not limit_value:
        return True, "ok"
    try:
        iv = int(limit_value)
    except Exception:
        return False, "limit_value is not an integer"
    if iv < 1 or iv > 1000:
        return False, "limit_value out of allowed range"
    return True, "ok"


def validate_filters(table: str, filters: str, schema: dict):
    if not filters:
        return True, "ok"

    allowed = set(schema[table]["columns"])

    column_candidates = extract_column_like_tokens(filters)

    # if dotted references appear in non-join single-table query, reject
    for token in column_candidates:
        if "." in token:
            return False, f"Dotted identifier '{token}' not allowed in single-table filters"

    # validate tokens that look like columns
    for token in column_candidates:
        # skip quoted text-ish values
        if token.lower() in {"and", "or"}:
            continue
        if token in allowed:
            continue

        # numeric values already excluded by regex
        # function names / aliases
        if token.lower() in {"avg", "max", "min", "sum", "count"}:
            continue

        # probably a value like marketing or Kasun → ignore
        # heuristic: if not in schema columns, treat it as possible literal
        pass

    # basic SQL safety
    low = filters.lower()
    blocked = ["insert ", "update ", "delete ", "drop ", "alter ", "truncate ", ";", "--"]
    if any(b in low for b in blocked):
        return False, "Unsafe token found in filters"

    return True, "ok"


def validate_joins(joins: str, schema: dict):
    if not joins:
        return True, "ok"

    low = joins.lower()
    blocked = ["insert ", "update ", "delete ", "drop ", "alter ", "truncate ", ";", "--"]
    if any(b in low for b in blocked):
        return False, "Unsafe token found in joins"

    known_tables = set(schema.keys())
    tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", joins)
    keywords = {"join", "on", "left", "right", "inner", "outer", "full", "where", "and", "or", "is", "null"}
    table_mentions = [t for t in tokens if t.lower() not in keywords and t in known_tables]

    if not table_mentions:
        return False, "No known tables found in joins"

    return True, "ok"


def validate_meaning(pred: dict, schema: dict):
    """
    Validate normalized meaning output before SQL building.
    Returns (bool, message)
    """
    if not isinstance(pred, dict):
        return False, "Prediction must be a dictionary"

    if pred.get("parse_error"):
        return False, "Prediction contains parse_error"

    table = clean_str(pred.get("target_table", ""))
    target_columns = clean_str(pred.get("target_columns", "*"))
    filters = clean_str(pred.get("filters", ""))
    joins = clean_str(pred.get("joins", ""))
    group_by = clean_str(pred.get("group_by", ""))
    sort_column = clean_str(pred.get("sort_column", ""))
    limit_value = clean_str(pred.get("limit_value", ""))
    intent = clean_str(pred.get("intent", ""))

    # join queries may not have single-table validation first
    if joins:
        ok, msg = validate_joins(joins, schema)
        if not ok:
            return False, msg
    else:
        ok, msg = validate_table(table, schema)
        if not ok:
            return False, msg

        ok, msg = validate_columns(table, target_columns, schema)
        if not ok:
            return False, msg

        ok, msg = validate_filters(table, filters, schema)
        if not ok:
            return False, msg

        ok, msg = validate_group_by(table, group_by, schema)
        if not ok:
            return False, msg

        ok, msg = validate_sort_column(table, sort_column, schema)
        if not ok:
            return False, msg

    ok, msg = validate_limit(limit_value)
    if not ok:
        return False, msg

    # intent sanity
    allowed_intents = {
        "select", "count", "aggregate",
        "group_by", "group_by_aggregate",
        "join_select", "join_aggregate", "multi_join_select",
        "nested_select"
    }
    if intent and intent not in allowed_intents:
        return False, f"Unsupported intent: {intent}"

    return True, "ok"