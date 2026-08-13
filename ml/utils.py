def meaning_to_sql(pred):
    table = pred.get("target_table", "").strip()
    cols = pred.get("target_columns", "*").strip() or "*"
    filters = pred.get("filters", "").strip()
    joins = pred.get("joins", "").strip()
    group_by = pred.get("group_by", "").strip()
    sort_column = pred.get("sort_column", "").strip()
    sort_order = pred.get("sort_order", "").strip()
    limit_value = pred.get("limit_value", "").strip()
    aggregate = pred.get("aggregate", "").strip()
    intent = pred.get("intent", "").strip()

    if not table:
        return None

    if aggregate == "count" or intent == "count":
        sql = f"SELECT COUNT(*) AS total FROM {table}"
    elif aggregate == "avg" and cols != "*":
        sql = f"SELECT AVG({cols}) AS avg_value FROM {table}"
    else:
        sql = f"SELECT {cols} FROM {table}"

    if joins:
        sql = f"SELECT {cols} FROM {joins}"

    if filters:
        # assumes filters already in SQL-like text from model training
        sql += f" WHERE {filters}"

    if group_by:
        sql += f" GROUP BY {group_by}"

    if sort_column:
        sql += f" ORDER BY {sort_column} {sort_order or 'ASC'}"

    if limit_value:
        sql += f" LIMIT {limit_value}"

    return sql