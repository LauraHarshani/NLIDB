from flask import Flask, render_template, request, send_file, redirect, url_for
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text
import re

from nl2sql.hybrid import nl_to_sql_hybrid
from nl2sql.schema_dynamic import get_live_schema

from ml.predict_meaning import predict_meaning
from ml.meaning_normalizer import normalize_meaning
from ml.validator import validate_meaning
from ml.sql_builder import meaning_to_sql
from ml.query_repair import auto_repair_meaning

MYSQL_USER = "nlidb_user"
MYSQL_PASS = "StrongPassword123!"
MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_DB   = "nlidb_db"

MYSQL_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
engine = create_engine(MYSQL_URL, pool_pre_ping=True)

APP_DIR = Path(__file__).resolve().parent
HISTORY_TABLE = "query_history"

app = Flask(__name__)

# DB SETUP
def init_history():
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
        id INT AUTO_INCREMENT PRIMARY KEY,
        nl_query TEXT NOT NULL,
        sql_query TEXT NOT NULL,
        status VARCHAR(20) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))


init_history()


def save_history(nlq: str, sqlq: str, status: str):
    insert_sql = text(f"""
        INSERT INTO {HISTORY_TABLE} (nl_query, sql_query, status)
        VALUES (:nlq, :sqlq, :status)
    """)
    with engine.begin() as conn:
        conn.execute(insert_sql, {"nlq": nlq, "sqlq": sqlq, "status": status})

# HELPERS
def mysqlize_named_params(sql: str):
    """
    Convert SQLAlchemy-style named params ':name' into PyMySQL named params '%(name)s'
    """
    if not sql:
        return sql
    return re.sub(r":([a-zA-Z_]\w*)", r"%(\1)s", sql)


def try_ml_pipeline(nl_query: str, schema: dict):
    try:
        raw_pred = predict_meaning(nl_query)
        norm_pred = normalize_meaning(raw_pred, schema)
        repaired_pred = auto_repair_meaning(norm_pred, schema)

        valid, msg = validate_meaning(repaired_pred, schema)

        if not valid:
            return {
                "ok": False,
                "error": f"ML validation failed: {msg}",
                "raw_prediction": raw_pred,
                "normalized_prediction": norm_pred,
                "repaired_prediction": repaired_pred,
            }

        sql = meaning_to_sql(repaired_pred, schema)

        return {
            "ok": True,
            "sql": sql,
            "params": {},
            "explanation": (
                f"Source=meaning_model | raw={raw_pred} | normalized={norm_pred} | repaired={repaired_pred}"
            ),
            "source": "meaning_model",
            "normalized_prediction": repaired_pred,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"ML pipeline exception: {e}"
        }

def run_with_fallback(nl_query: str):
    """
    Try ML meaning model first.
    If it fails, fallback to your current nl_to_sql_hybrid().
    """
    schema = get_live_schema(engine)

    # 1) Try ML meaning pipeline
    ml_result = try_ml_pipeline(nl_query, schema)
    if ml_result.get("ok"):
        return ml_result

    # 2) Fallback to your current hybrid parser
    fallback = nl_to_sql_hybrid(nl_query, engine=engine)

    if fallback.get("ok"):
        extra = ml_result.get("error", "unknown_ml_failure")
        fallback["explanation"] = (
            f"{fallback.get('explanation', '')} | fallback_used=True | ml_failed={extra}"
        ).strip(" |")
        fallback["source"] = fallback.get("source", "hybrid_fallback")

    return fallback


# =========================================================
# ROUTES
# =========================================================
@app.get("/")
def index():
    return render_template(
        "index.html",
        result=None,
        error=None,
        sql=None,
        explanation=None,
        nl_query="",
        columns=None
    )

@app.post("/query")
def run_query():
    nl_query = request.form.get("nl_query", "").strip()

    parsed = run_with_fallback(nl_query)

    if not parsed.get("ok"):
        save_history(nl_query, parsed.get("error", "parse_failed"), "FAILED")
        return render_template(
            "index.html",
            result=None,
            error=parsed.get("error"),
            sql=None,
            explanation=None,
            nl_query=nl_query,
            columns=None
        )

    sql = parsed["sql"]
    params = parsed.get("params", {}) or {}
    explanation = parsed.get("explanation", "")

    # Convert :param -> %(param)s for MySQL/PyMySQL
    sql_exec = mysqlize_named_params(sql)

    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(sql_exec, conn, params=params)

        save_history(nl_query, sql, "OK")

        out_path = APP_DIR / "db" / "last_result.csv"
        out_path.parent.mkdir(exist_ok=True)
        df.to_csv(out_path, index=False)

        return render_template(
            "index.html",
            result=df.to_dict(orient="records"),
            columns=list(df.columns),
            error=None,
            sql=sql,
            explanation=explanation,
            nl_query=nl_query
        )

    except Exception as e:
        save_history(nl_query, sql, "FAILED")
        return render_template(
            "index.html",
            result=None,
            error=str(e),
            sql=sql,
            explanation=explanation,
            nl_query=nl_query,
            columns=None
        )

@app.get("/history")
def history():
    with engine.connect() as conn:
        df = pd.read_sql_query(
            f"SELECT * FROM {HISTORY_TABLE} ORDER BY id DESC LIMIT 200",
            conn
        )
    return render_template("history.html", rows=df.to_dict(orient="records"))

@app.get("/export/csv")
def export_csv():
    path = APP_DIR / "db" / "last_result.csv"
    if not path.exists():
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="query_results.csv")

if __name__ == "__main__":
    app.run(debug=True)