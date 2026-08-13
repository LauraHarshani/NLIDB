import re
from nl2sql.schema_dynamic import get_live_schema
from nl2sql.sql_safety import is_safe_select, references_only_known_schema
from nl2sql.parser_rules import nl_to_sql as rules_nl_to_sql
from nl2sql.transformer_sql import T5Text2SQL

MODEL_ID = "cssupport/t5-small-awesome-text-to-sql"
_t5 = None

def _get_t5():
    global _t5
    if _t5 is None:
        _t5 = T5Text2SQL(MODEL_ID)
    return _t5

def _intent(nl: str):
    q = (nl or "").lower()
    wants_count = any(x in q for x in ["how many", "count", "number of", "total"])
    wants_list = any(x in q for x in ["list", "show", "display", "all", "give me", "fetch"])
    return wants_count, wants_list

def _has_count(sql: str):
    return bool(re.search(r"\bcount\s*\(", (sql or "").lower()))

def nl_to_sql_hybrid(nl_query: str, engine=None, schema: dict = None):
    if schema is None and engine is not None:
        schema = get_live_schema(engine)
    wants_count, wants_list = _intent(nl_query)

    # 1) Transformer attempt
    try:
        t5 = _get_t5()
        tr = t5.generate_sql(nl_query, schema)

        if tr.ok and tr.sql:
            # intent guard
            if wants_list and _has_count(tr.sql):
                raise ValueError("intent_mismatch: list requested but COUNT found")
            if wants_count and not _has_count(tr.sql):
                raise ValueError("intent_mismatch: count requested but COUNT not found")

            safe, msg = is_safe_select(tr.sql)
            if not safe:
                raise ValueError(f"unsafe_sql: {msg}")

            schema_ok, s_msg = references_only_known_schema(tr.sql, schema)
            if not schema_ok:
                raise ValueError(f"schema_invalid: {s_msg}")

            return {
                "ok": True,
                "sql": tr.sql,
                "params": {},
                "explanation": f"Transformer: {MODEL_ID}",
                "source": "transformer",
            }

    except Exception as e:
        # 2) Rules fallback (fixed)
        r = rules_nl_to_sql(nl_query, schema)
        if r.get("ok"):
            r["source"] = "rules_fallback"
            r["explanation"] = (r.get("explanation", "") + f" | transformer_rejected={e}").strip()
        return r

    # If transformer didn't produce SQL, fallback
    r = rules_nl_to_sql(nl_query, schema)
    if r.get("ok"):
        r["source"] = "rules_fallback"
        r["explanation"] = (r.get("explanation", "") + " | transformer_rejected=no_sql").strip()
    return r