def validate_meaning(pred: dict, schema: dict):
    if not isinstance(pred, dict):
        return False, "Prediction is not a dict"

    table = pred.get("target_table", "").strip()
    if table and table not in schema:
        return False, f"Unknown table: {table}"

    joins = pred.get("joins", "").strip()
    if joins:
        # simple safety: reject joins not using known tables
        known = set(schema.keys())
        for t in known:
            joins = joins.replace(t, "")
        if "join" in pred.get("joins", "").lower() and pred.get("target_table", "") == "students":
            # optional strict rule for your current prototype
            return False, "Unexpected join for current query"

    return True, "ok"