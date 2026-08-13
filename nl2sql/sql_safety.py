import re
import sqlglot
from sqlglot import expressions as exp

BLOCKLIST = [
    "insert", "update", "delete", "drop", "alter", "truncate",
    "create", "replace", "grant", "revoke", "shutdown",
    "load_file", "outfile", "dumpfile", "sleep", "benchmark",
]

BAD_FAKE_TABLES = {"mysql", "tables", "table", "database"}

def is_safe_select(sql: str):
    s = (sql or "").strip()
    if not s:
        return False, "empty"

    low = s.lower()
    if not low.startswith("select"):
        return False, "only SELECT allowed"

    for kw in BLOCKLIST:
        if re.search(rf"\b{re.escape(kw)}\b", low):
            return False, f"blocked keyword: {kw}"

    try:
        ast = sqlglot.parse_one(s, read="mysql")
        if not isinstance(ast, exp.Select):
            return False, "not a SELECT"
        return True, "ok"
    except Exception as e:
        return False, f"parse_error: {e}"

def references_only_known_schema(sql: str, schema: dict):
    """
    Validate base tables + columns exist.
    Handles aliases safely.
    """
    try:
        ast = sqlglot.parse_one(sql, read="mysql")
        known_tables = set(schema.keys())
        known_cols = {t: set(schema[t]["columns"]) for t in schema}

        # Collect real table names (ignore aliases)
        used_tables = set()
        for t in ast.find_all(exp.Table):
            # exp.Table.this is Identifier
            name = t.name
            if name:
                used_tables.add(name)

        for t in used_tables:
            tl = t.lower()
            if tl in BAD_FAKE_TABLES:
                return False, f"Model used fake table token: {t}"
            if t not in known_tables:
                return False, f"Unknown table: {t}"

        # Validate columns (best-effort)
        for c in ast.find_all(exp.Column):
            col_name = c.name
            if not col_name:
                continue
            # If no explicit table, check it exists in at least one table
            if not c.table:
                if not any(col_name in cols for cols in known_cols.values()):
                    return False, f"Unknown column: {col_name}"
            else:
                # Table may be alias, skip strict alias resolution here
                # but still reject if column doesn't exist anywhere
                if not any(col_name in cols for cols in known_cols.values()):
                    return False, f"Unknown column: {col_name}"

        return True, "ok"

    except Exception as e:
        return False, f"sql_parse_error: {e}"