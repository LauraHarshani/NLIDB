def schema_to_ddl(schema: dict) -> str:
    """
    Converts schema_dynamic.load_schema output into DDL-like text for grounding.
    """
    lines = []
    for table, meta in schema.items():
        cols = meta.get("columns", [])
        if not cols:
            continue
        col_defs = ", ".join([f"{c} TEXT" for c in cols])
        lines.append(f"CREATE TABLE {table} ({col_defs});")
    return "\n".join(lines)