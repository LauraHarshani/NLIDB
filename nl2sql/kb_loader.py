import json
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent / "kb"

def load_kb_files():
    tables = json.loads((KB_DIR / "kb_tables.json").read_text(encoding="utf-8"))
    columns = json.loads((KB_DIR / "kb_columns.json").read_text(encoding="utf-8"))
    examples = json.loads((KB_DIR / "kb_examples.json").read_text(encoding="utf-8"))
    return tables, columns, examples

def user_schema_from_kb(kb_tables: dict, kb_columns: dict):
    """
    Build schema dict used by rules + transformer:
      { table: {columns, text_cols, num_cols} }
    Excludes system tables.
    """
    schema = {}
    for table, meta in kb_tables.items():
        if meta.get("is_system"):
            continue
        cols = kb_columns.get(table, [])
        col_names = [c["column_name"] for c in cols]

        text_cols, num_cols = [], []
        for c in cols:
            dt = (c.get("data_type") or "").lower()
            if any(x in dt for x in ["int", "double", "float", "decimal"]):
                num_cols.append(c["column_name"])
            else:
                text_cols.append(c["column_name"])

        schema[table] = {"columns": col_names, "text_cols": text_cols, "num_cols": num_cols}
    return schema