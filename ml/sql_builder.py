import re


ALLOWED_OPERATORS = {
    "=", ">", "<", ">=", "<=", "!=", "<>", "LIKE", "BETWEEN", "IN", "IS NULL", "IS NOT NULL"
}


def clean_str(value):
    if value is None:
        return ""
    return str(value).strip()


def split_csv_like(value: str):
    value = clean_str(value)
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def is_safe_identifier(name: str):
    """
    Allow only table/column style identifiers:
    letters, numbers, underscore, dot
    """
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\.]*", name.strip()))


def sanitize_identifier(name: str):
    name = clean_str(name)
    if not is_safe_identifier(name):
        raise ValueError(f"Unsafe identifier: {name}")
    return name


def sanitize_select_columns(cols: str):
    cols = clean_str(cols)
    if not cols or cols == "*":
        return "*"

    parts = split_csv_like(cols)
    safe_parts = [sanitize_identifier(p) for p in parts]
    return ", ".join(safe_parts)


def sanitize_table_name(table: str, schema: dict):
    table = clean_str(table)
    if table not in schema:
        raise ValueError(f"Unknown table: {table}")
    return table


def sanitize_joins(joins: str, schema: dict):
    """
    Basic join validation.
    Example accepted:
      students JOIN enrollments ON students.student_id = enrollments.student_id
    """
    joins = clean_str(joins)
    if not joins:
        return ""

    # very basic keyword safety
    low = joins.lower()
    blocked = ["insert", "update", "delete", "drop", "alter", "truncate", ";"]
    if any(b in low for b in blocked):
        raise ValueError("Unsafe join clause")

    # validate referenced table names loosely
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", joins)
    join_keywords = {"join", "on", "left", "right", "inner", "outer", "full", "where", "and", "or", "is", "null"}
    candidate_tables = [t for t in tokens if t.lower() not in join_keywords]

    known_tables = set(schema.keys())
    table_hits = [t for t in candidate_tables if t in known_tables]

    if not table_hits:
        raise ValueError("No valid known tables found in joins")

    return joins


def sanitize_filters(filters: str):
    """
    We assume filters come from your trained meaning model.
    Still block dangerous SQL.
    """
    filters = clean_str(filters)
    if not filters:
        return ""

    low = filters.lower()
    blocked = ["insert", "update", "delete", "drop", "alter", "truncate", ";", "--"]
    if any(b in low for b in blocked):
        raise ValueError("Unsafe filters")

    # convert semicolon-separated learned filters into SQL AND
    filters = filters.replace(";", " AND ")
    filters = re.sub(r"\s+", " ", filters).strip()
    return filters


def sanitize_group_by(group_by: str):
    group_by = clean_str(group_by)
    if not group_by:
        return ""
    return ", ".join(sanitize_identifier(x) for x in split_csv_like(group_by))


def sanitize_sort_column(sort_column: str):
    sort_column = clean_str(sort_column)
    if not sort_column:
        return ""
    return sanitize_identifier(sort_column)


def sanitize_sort_order(sort_order: str):
    sort_order = clean_str(sort_order).upper()
    if not sort_order:
        return ""
    if sort_order not in {"ASC", "DESC"}:
        raise ValueError(f"Invalid sort order: {sort_order}")
    return sort_order


def sanitize_limit(limit_value):
    if limit_value is None or clean_str(limit_value) == "":
        return ""
    try:
        iv = int(float(limit_value))
    except Exception:
        raise ValueError(f"Invalid limit value: {limit_value}")
    if iv < 1 or iv > 1000:
        raise ValueError("Limit out of allowed range")
    return iv


def build_select_sql(pred: dict, schema: dict):
    table = sanitize_table_name(pred.get("target_table", ""), schema)
    columns = sanitize_select_columns(pred.get("target_columns", "*"))
    filters = sanitize_filters(pred.get("filters", ""))
    sort_column = sanitize_sort_column(pred.get("sort_column", ""))
    sort_order = sanitize_sort_order(pred.get("sort_order", "")) or "ASC"
    limit_value = sanitize_limit(pred.get("limit_value", ""))

    sql = f"SELECT {columns} FROM {table}"

    if filters:
        sql += f" WHERE {filters}"

    if sort_column:
        sql += f" ORDER BY {sort_column} {sort_order}"

    if limit_value:
        sql += f" LIMIT {limit_value}"

    return sql


def build_count_sql(pred: dict, schema: dict):
    table = sanitize_table_name(pred.get("target_table", ""), schema)
    filters = sanitize_filters(pred.get("filters", ""))

    sql = f"SELECT COUNT(*) AS total FROM {table}"
    if filters:
        sql += f" WHERE {filters}"
    return sql


def build_aggregate_sql(pred: dict, schema: dict):
    table = sanitize_table_name(pred.get("target_table", ""), schema)
    aggregate = clean_str(pred.get("aggregate", "")).lower()
    columns = sanitize_select_columns(pred.get("target_columns", "*"))
    filters = sanitize_filters(pred.get("filters", ""))

    # use first column if many were passed
    target_col = columns
    if "," in columns:
        target_col = columns.split(",")[0].strip()

    if aggregate == "count":
        sql = f"SELECT COUNT(*) AS total FROM {table}"
    elif aggregate == "count_distinct":
        sql = f"SELECT COUNT(DISTINCT {target_col}) AS total_distinct FROM {table}"
    elif aggregate == "avg":
        sql = f"SELECT AVG({target_col}) AS avg_value FROM {table}"
    elif aggregate == "sum":
        sql = f"SELECT SUM({target_col}) AS total_value FROM {table}"
    elif aggregate == "max":
        sql = f"SELECT MAX({target_col}) AS max_value FROM {table}"
    elif aggregate == "min":
        sql = f"SELECT MIN({target_col}) AS min_value FROM {table}"
    else:
        raise ValueError(f"Unsupported aggregate type: {aggregate}")

    if filters:
        sql += f" WHERE {filters}"

    return sql


def build_group_by_sql(pred: dict, schema: dict):
    table = sanitize_table_name(pred.get("target_table", ""), schema)
    group_by = sanitize_group_by(pred.get("group_by", ""))
    aggregate = clean_str(pred.get("aggregate", "")).lower()
    columns = sanitize_select_columns(pred.get("target_columns", "*"))
    filters = sanitize_filters(pred.get("filters", ""))
    sort_column = sanitize_sort_column(pred.get("sort_column", ""))
    sort_order = sanitize_sort_order(pred.get("sort_order", "")) or "ASC"
    limit_value = sanitize_limit(pred.get("limit_value", ""))

    if not group_by:
        raise ValueError("group_by is required for group-by query")

    # choose aggregate target column
    target_col = columns
    if columns == "*" or not columns:
        # fallback to sort column if available
        target_col = sort_column or group_by.split(",")[0].strip()
    elif "," in columns:
        target_col = columns.split(",")[0].strip()

    if aggregate == "count":
        agg_expr = "COUNT(*) AS total"
    elif aggregate == "count_distinct":
        agg_expr = f"COUNT(DISTINCT {target_col}) AS total_distinct"
    elif aggregate == "avg":
        agg_expr = f"AVG({target_col}) AS avg_value"
    elif aggregate == "sum":
        agg_expr = f"SUM({target_col}) AS total_value"
    elif aggregate == "max":
        agg_expr = f"MAX({target_col}) AS max_value"
    elif aggregate == "min":
        agg_expr = f"MIN({target_col}) AS min_value"
    else:
        raise ValueError(f"Unsupported group aggregate: {aggregate}")

    sql = f"SELECT {group_by}, {agg_expr} FROM {table}"

    if filters:
        sql += f" WHERE {filters}"

    sql += f" GROUP BY {group_by}"

    if sort_column:
        sql += f" ORDER BY {sort_column} {sort_order}"

    if limit_value:
        sql += f" LIMIT {limit_value}"

    return sql


def build_join_sql(pred: dict, schema: dict):
    joins = sanitize_joins(pred.get("joins", ""), schema)
    if not joins:
        raise ValueError("joins is required for join query")

    columns = sanitize_select_columns(pred.get("target_columns", "*"))
    filters = sanitize_filters(pred.get("filters", ""))
    group_by = sanitize_group_by(pred.get("group_by", ""))
    sort_column = sanitize_sort_column(pred.get("sort_column", ""))
    sort_order = sanitize_sort_order(pred.get("sort_order", "")) or "ASC"
    limit_value = sanitize_limit(pred.get("limit_value", ""))

    sql = f"SELECT {columns} FROM {joins}"

    if filters:
        sql += f" WHERE {filters}"

    if group_by:
        sql += f" GROUP BY {group_by}"

    if sort_column:
        sql += f" ORDER BY {sort_column} {sort_order}"

    if limit_value:
        sql += f" LIMIT {limit_value}"

    return sql


def build_nested_sql(pred: dict, schema: dict):
    """
    For now nested queries are treated like validated raw WHERE conditions,
    because your model already predicts nested expressions in `filters`.
    """
    table = sanitize_table_name(pred.get("target_table", ""), schema)
    columns = sanitize_select_columns(pred.get("target_columns", "*"))
    filters = sanitize_filters(pred.get("filters", ""))

    if not filters:
        raise ValueError("Nested query requires filters")

    sql = f"SELECT {columns} FROM {table} WHERE {filters}"
    return sql


def meaning_to_sql(pred: dict, schema: dict):
    """
    Main dispatcher.
    pred = model output dict
    schema = live schema dict from your DB
    """
    if not isinstance(pred, dict):
        raise ValueError("Prediction must be a dictionary")

    intent = clean_str(pred.get("intent", "")).lower()
    aggregate = clean_str(pred.get("aggregate", "")).lower()
    joins = clean_str(pred.get("joins", ""))
    group_by = clean_str(pred.get("group_by", ""))

    # routing rules
    if intent == "count":
        return build_count_sql(pred, schema)

    if intent in {"aggregate"} and aggregate:
        return build_aggregate_sql(pred, schema)

    if intent in {"group_by", "group_by_aggregate"} or group_by:
        return build_group_by_sql(pred, schema)

    if intent in {"join_select", "join_aggregate", "multi_join_select"} or joins:
        return build_join_sql(pred, schema)

    if intent in {"nested_select"}:
        return build_nested_sql(pred, schema)

    return build_select_sql(pred, schema)