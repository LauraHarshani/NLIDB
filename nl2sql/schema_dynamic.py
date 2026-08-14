import re
from sqlalchemy import text

EXCLUDE_TABLES = {"query_history"}

def normalize(s: str) -> str:
    """
    Normalize strings for fuzzy matching:
    - lower
    - remove non-alphanumerics
    - collapse spaces/underscores
    """
    s = str(s or "").strip().lower()
    s = s.replace("_", " ")
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

from sqlalchemy import inspect

def get_live_schema(engine, user_id=None):
    schema = {}
    insp = inspect(engine)
    
    user_prefix = f"u{user_id}_" if user_id else None
    
    for t in insp.get_table_names():
        if t in EXCLUDE_TABLES or t == "users":
            continue
        if t.startswith("kb_"):
            continue
            
        # If user_id is provided, only include tables with their prefix
        if user_prefix:
            if not t.startswith(user_prefix):
                continue
        
        # When displaying, we can optionally strip the prefix, but for SQL generation we need the real name
        schema.setdefault(t, {"columns": [], "text_cols": [], "num_cols": []})
        
        for col_info in insp.get_columns(t):
            col = col_info["name"]
            dt = str(col_info["type"]).lower()
            
            schema[t]["columns"].append(col)
            
            if any(x in dt for x in ["int", "decimal", "float", "double", "numeric", "real"]):
                schema[t]["num_cols"].append(col)
            else:
                schema[t]["text_cols"].append(col)
                
    return schema