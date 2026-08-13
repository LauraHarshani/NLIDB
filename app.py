from flask import Flask, render_template, request, send_file, redirect, url_for, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine, text
import re
import os
from werkzeug.utils import secure_filename

from nl2sql.hybrid import nl_to_sql_hybrid
from nl2sql.schema_dynamic import get_live_schema
from nl2sql.gemini_engine import generate_sql_with_gemini

from ml.predict_meaning import predict_meaning
from ml.meaning_normalizer import normalize_meaning
from ml.validator import validate_meaning
from ml.sql_builder import meaning_to_sql

# MYSQL_USER = "nlidb_user"
# MYSQL_PASS = "StrongPassword123!"
# MYSQL_HOST = "127.0.0.1"
# MYSQL_PORT = 3306
# MYSQL_DB   = "nlidb_db"
# MYSQL_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASS}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

APP_DIR = Path(__file__).resolve().parent

# Use local SQLite database instead
SQLITE_DB_PATH = APP_DIR / "db" / "sample.db"
SQLITE_URL = f"sqlite:///{SQLITE_DB_PATH}"

engine = create_engine(SQLITE_URL, pool_pre_ping=True)
HISTORY_TABLE = "query_history"

app = Flask(__name__)
app.secret_key = "super_secret_key_v2_nlidb"

UPLOAD_FOLDER = APP_DIR / 'uploads'
UPLOAD_FOLDER.mkdir(exist_ok=True)
app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, id, username, name=None, created_at=None):
        self.id = id
        self.username = username
        self.name = name
        self.created_at = created_at

@login_manager.user_loader
def load_user(user_id):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, username, name, created_at FROM users WHERE id = :id"), {"id": user_id}).mappings().first()
        if row:
            # Simple format: YYYY-MM-DD
            created_at_str = str(row.get("created_at")).split()[0] if row.get("created_at") else "Recently"
            return User(id=row["id"], username=row["username"], name=row.get("name"), created_at=created_at_str)
    return None

# DB SETUP
def init_history():
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {HISTORY_TABLE} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        nl_query TEXT NOT NULL,
        sql_query TEXT NOT NULL,
        status VARCHAR(20) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    create_formulas_sql = """
    CREATE TABLE IF NOT EXISTS column_formulas (
        table_name VARCHAR(255),
        column_name VARCHAR(255),
        formula TEXT,
        PRIMARY KEY (table_name, column_name)
    );
    """
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))
        conn.execute(text(create_formulas_sql))
        try:
            conn.execute(text(f"ALTER TABLE {HISTORY_TABLE} ADD COLUMN user_id INTEGER"))
        except Exception:
            pass

init_history()


def save_history(nlq: str, sqlq: str, status: str, user_id: int = None):
    insert_sql = text(f"""
        INSERT INTO {HISTORY_TABLE} (user_id, nl_query, sql_query, status)
        VALUES (:uid, :nlq, :sqlq, :status)
    """)
    with engine.begin() as conn:
        conn.execute(insert_sql, {"uid": user_id, "nlq": nlq, "sqlq": sqlq, "status": status})

# HELPERS
def format_named_params(sql: str):
    """
    Convert SQLAlchemy-style named params ':name' based on dialect.
    """
    if not sql:
        return sql
    if engine.dialect.name == "mysql":
        return re.sub(r":([a-zA-Z_]\w*)", r"%(\1)s", sql)
    # SQLite works fine with :name syntax
    return sql


def try_ml_pipeline(nl_query: str, schema: dict):
    """
    ML pipeline:
      user input
      -> predict meaning
      -> normalize meaning
      -> validate meaning
      -> build SQL

    Returns a dict matching your existing parsed structure:
      {
        ok: bool,
        sql: str,
        params: dict,
        explanation: str,
        source: str
      }
    """
    try:
        raw_pred = predict_meaning(nl_query)
        norm_pred = normalize_meaning(raw_pred, schema)
        valid, msg = validate_meaning(norm_pred, schema)

        if not valid:
            return {
                "ok": False,
                "error": f"ML validation failed: {msg}",
                "raw_prediction": raw_pred,
                "normalized_prediction": norm_pred,
            }

        sql = meaning_to_sql(norm_pred, schema)

        return {
            "ok": True,
            "sql": sql,
            "params": {},   # ML builder currently returns final SQL directly
            "explanation": (
                f"Source=meaning_model | raw={raw_pred} | normalized={norm_pred}"
            ),
            "source": "meaning_model",
            "normalized_prediction": norm_pred,
        }

    except Exception as e:
        return {
            "ok": False,
            "error": f"ML pipeline exception: {e}"
        }


def run_with_fallback(nl_query: str, user_id=None, target_table=None):
    """
    Try Gemini AI model first.
    If it fails, fallback to ML pipeline, then to hybrid.
    """
    schema = get_live_schema(engine, user_id)
    
    if target_table and target_table != "all" and target_table in schema:
        schema = {target_table: schema[target_table]}

    # 1) Try Gemini AI
    gemini_result = generate_sql_with_gemini(nl_query, schema)
    if gemini_result.get("ok"):
        return gemini_result

    # 2) Try ML meaning pipeline
    ml_result = try_ml_pipeline(nl_query, schema)
    if ml_result.get("ok"):
        return ml_result

    # 2) Fallback to your current hybrid parser
    fallback = nl_to_sql_hybrid(nl_query, engine=engine, schema=schema)

    if fallback.get("ok"):
        extra = ml_result.get("error", "unknown_ml_failure")
        fallback["explanation"] = (
            f"{fallback.get('explanation', '')} | fallback_used=True | ml_failed={extra}"
        ).strip(" |")
        fallback["source"] = fallback.get("source", "hybrid_fallback")

    return fallback

# ROUTES
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name").strip()
        email = request.form.get("email").strip()
        password = request.form.get("password")
        
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        
        try:
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO users (username, password, name) VALUES (:u, :p, :n)"), {"u": email, "p": hashed, "n": name})
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except Exception as e:
            print("REGISTRATION ERROR:", str(e))
            flash(f"Error: {str(e)}", "danger")
            
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email").strip()
        password = request.form.get("password")
        
        with engine.connect() as conn:
            user = conn.execute(text("SELECT * FROM users WHERE username = :u"), {"u": email}).mappings().first()
            
        if user and bcrypt.check_password_hash(user["password"], password):
            login_user(User(id=user["id"], username=user["username"], name=user.get("name")))
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid credentials.", "danger")
            
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "update_name":
            new_name = request.form.get("name").strip()
            if new_name:
                try:
                    with engine.begin() as conn:
                        conn.execute(text("UPDATE users SET name = :n WHERE id = :id"), {"n": new_name, "id": current_user.id})
                    current_user.name = new_name
                    flash("Name updated successfully.", "success")
                except Exception as e:
                    flash(f"Error updating name: {e}", "danger")
                    
        elif action == "update_password":
            old_password = request.form.get("old_password")
            new_password = request.form.get("new_password")
            confirm_password = request.form.get("confirm_password")
            
            if new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                return redirect(url_for("settings"))
                
            if new_password and old_password:
                # Check old password first
                with engine.connect() as conn:
                    user_data = conn.execute(text("SELECT password FROM users WHERE id = :id"), {"id": current_user.id}).mappings().first()
                    
                if user_data and bcrypt.check_password_hash(user_data["password"], old_password):
                    hashed = bcrypt.generate_password_hash(new_password).decode('utf-8')
                    try:
                        with engine.begin() as conn:
                            conn.execute(text("UPDATE users SET password = :p WHERE id = :id"), {"p": hashed, "id": current_user.id})
                        flash("Password updated successfully.", "success")
                    except Exception as e:
                        flash(f"Error updating password: {e}", "danger")
                else:
                    flash("Incorrect current password.", "danger")
                    
        return redirect(url_for("settings"))
        
    return render_template("settings.html", current_user=current_user)

@app.route("/dashboard")
@login_required
def dashboard():
    schema = get_live_schema(engine, current_user.id)
    table_count = len(schema)
    tables = list(schema.keys())
    # Try to get the 3 most recently created tables (we'll just use the last 3 from schema keys for now as they are usually appended)
    recent_tables = tables[-3:] if len(tables) >= 3 else tables
    
    with engine.connect() as conn:
        history_count = conn.execute(text(f"SELECT COUNT(*) FROM {HISTORY_TABLE}")).scalar()
        recent_queries_result = conn.execute(
            text(f"SELECT nl_query, status FROM {HISTORY_TABLE} ORDER BY id DESC LIMIT 3")
        ).mappings().fetchall()
        recent_queries = [dict(r) for r in recent_queries_result]
        
    return render_template(
        "dashboard.html", 
        username=current_user.username, 
        table_count=table_count, 
        history_count=history_count,
        recent_tables=recent_tables,
        recent_queries=recent_queries
    )

@app.route("/ask")
@login_required
def ask_page():
    schema = get_live_schema(engine, current_user.id)
    tables = list(schema.keys())
    return render_template("query.html", username=current_user.username, tables=tables, target_tables=[])

@app.route("/")
def index():
    # If logged in, go to dashboard, else login
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.post("/query")
@login_required
def run_query():
    nl_query = request.form.get("nl_query", "").strip()
    target_tables = request.form.getlist("target_tables")
    
    schema = get_live_schema(engine, current_user.id)
    tables = list(schema.keys())

    if not target_tables:
        target_tables = tables
        
    all_results = []
    all_sqls = []
    errors = []

    for t in target_tables:
        if t not in tables:
            continue
            
        parsed = run_with_fallback(nl_query, current_user.id, target_table=t)
        
        if parsed.get("ok"):
            sql = parsed["sql"]
            if sql == "NOT_APPLICABLE":
                continue
            sql_exec = format_named_params(sql)
            try:
                with engine.connect() as conn:
                    df = pd.read_sql_query(sql_exec, conn, params=parsed.get("params", {}))
                    if not df.empty:
                        display_t = t.split("_", 1)[1] if "_" in t else t
                        
                        # Fetch full table data for contextual charting
                        full_df = pd.read_sql_query(f"SELECT * FROM {t}", conn)
                        
                        all_results.append({
                            "table_name": display_t,
                            "data": df.to_dict(orient="records"),
                            "columns": list(df.columns),
                            "full_data": full_df.to_dict(orient="records")
                        })
                        all_sqls.append(sql)
            except Exception:
                pass
        else:
            errors.append(f"{t}: {parsed.get('error', 'parse_failed')}")
            
    if all_results:
        combined_sql = "\nUNION ALL\n".join(all_sqls)
        save_history(nl_query, combined_sql, "OK", current_user.id)
        return render_template(
            "query.html",
            results=all_results,
            error=None,
            sql=combined_sql,
            explanation="Searched across selected tables.",
            nl_query=nl_query,
            tables=tables,
            target_tables=target_tables
        )
    else:
        err_msg = " | ".join(errors) if errors else "No results found in any of the selected tables."
        save_history(nl_query, err_msg, "FAILED" if errors else "OK", current_user.id)
        return render_template(
            "query.html",
            results=[],
            error=err_msg,
            sql=None,
            explanation=None,
            nl_query=nl_query,
            tables=tables,
            target_tables=target_tables
        )

@app.get("/history")
@login_required
def history():
    with engine.connect() as conn:
        df = pd.read_sql_query(
            f"SELECT * FROM {HISTORY_TABLE} WHERE user_id = :uid ORDER BY id DESC LIMIT 200",
            conn,
            params={"uid": current_user.id}
        )
    return render_template("history.html", rows=df.to_dict(orient="records"))

@app.route("/manager", methods=["GET", "POST"])
@login_required
def manager():
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "create_table":
            table_name = request.form.get("table_name").strip().lower()
            table_name = re.sub(r'[^a-z0-9_]', '', table_name)
            
            if not table_name:
                flash("Invalid table name.", "danger")
                return redirect(url_for("manager"))
                
            cols_input = request.form.getlist("columns[]")
            cols = [c.strip().lower() for c in cols_input if c.strip()]
            
            if not cols:
                flash("You must provide at least one column.", "danger")
                return redirect(url_for("manager"))
                
            actual_table_name = f"u{current_user.id}_{table_name}"
            
            col_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
            for c in cols:
                clean_col = re.sub(r'[^a-z0-9_]', '', c)
                col_defs.append(f"{clean_col} TEXT")
                
            create_sql = f"CREATE TABLE IF NOT EXISTS {actual_table_name} ({', '.join(col_defs)});"
            try:
                with engine.begin() as conn:
                    conn.execute(text(create_sql))
                flash(f"Table '{table_name}' created successfully!", "success")
                return redirect(url_for("view_table", table=actual_table_name))
            except Exception as e:
                flash(f"Failed to create table: {e}", "danger")
                
        elif action == "rename_table":
            old_name = request.form.get("old_table_name")
            new_name = request.form.get("new_table_name", "").strip().lower()
            new_name = re.sub(r'[^a-z0-9_]', '', new_name)
            
            if not old_name or not new_name:
                flash("Invalid table names.", "danger")
                return redirect(url_for("manager"))
                
            schema = get_live_schema(engine, current_user.id)
            if old_name not in schema:
                flash("Table not found or unauthorized.", "danger")
                return redirect(url_for("manager"))
                
            actual_new_name = f"u{current_user.id}_{new_name}"
            
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {old_name} RENAME TO {actual_new_name}"))
                flash(f"Table renamed to '{new_name}' successfully!", "success")
            except Exception as e:
                flash(f"Failed to rename table: {e}", "danger")
                
        elif action == "delete_table":
            table_name = request.form.get("table_name")
            
            if not table_name:
                flash("Invalid table name.", "danger")
                return redirect(url_for("manager"))
                
            schema = get_live_schema(engine, current_user.id)
            if table_name not in schema:
                flash("Table not found or unauthorized.", "danger")
                return redirect(url_for("manager"))
                
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"DROP TABLE {table_name}"))
                flash(f"Table deleted successfully!", "success")
            except Exception as e:
                flash(f"Failed to delete table: {e}", "danger")
                
        return redirect(url_for("manager"))
        
    schema = get_live_schema(engine, current_user.id)
    return render_template("manager.html", schema=schema, current_user_id=current_user.id)

@app.route("/manager/<table>", methods=["GET", "POST"])
@login_required
def view_table(table):
    schema = get_live_schema(engine, current_user.id)
    if table not in schema:
        flash("Table not found or unauthorized.", "danger")
        return redirect(url_for("manager"))
        
    cols = schema[table]["columns"]

    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "insert_row":
            val_dict = {}
            for c in cols:
                if c == "id": continue
                val_dict[c] = request.form.get(f"col_{c}", "")
                
            insert_cols = [c for c in cols if c != "id"]
            if insert_cols:
                cols_sql = ", ".join(insert_cols)
                vals_sql = ", ".join([f":{c}" for c in insert_cols])
                insert_sql = f"INSERT INTO {table} ({cols_sql}) VALUES ({vals_sql})"
                
                try:
                    with engine.begin() as conn:
                        conn.execute(text(insert_sql), val_dict)
                    flash("Row inserted successfully!", "success")
                except Exception as e:
                    flash(f"Failed to insert row: {e}", "danger")
                    
        elif action == "delete_row":
            row_id = request.form.get("row_id")
            if row_id:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
                    flash("Row deleted.", "success")
                except Exception as e:
                    flash(f"Failed to delete row: {e}", "danger")
                    
        elif action == "update_row":
            row_id = request.form.get("row_id")
            if row_id:
                val_dict = {"id": row_id}
                update_clauses = []
                for c in cols:
                    if c == "id": continue
                    val = request.form.get(f"col_{c}")
                    if val is not None:
                        val_dict[c] = val
                        update_clauses.append(f"{c} = :{c}")
                        
                if update_clauses:
                    update_sql = f"UPDATE {table} SET {', '.join(update_clauses)} WHERE id = :id"
                    try:
                        with engine.begin() as conn:
                            conn.execute(text(update_sql), val_dict)
                        flash("Row updated successfully!", "success")
                    except Exception as e:
                        flash(f"Failed to update row: {e}", "danger")
                    
        elif action == "rename_column":
            old_col = request.form.get("old_col")
            new_col = request.form.get("new_col").strip().lower()
            new_col = re.sub(r'[^a-z0-9_]', '', new_col)
            
            if old_col in cols and new_col and new_col != old_col:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}"))
                    flash(f"Column renamed to {new_col}.", "success")
                except Exception as e:
                    flash(f"Failed to rename column (ensure you are using SQLite 3.25.0+): {e}", "danger")
                    
        elif action == "add_column":
            new_col = request.form.get("new_col", "").strip().lower()
            new_col = re.sub(r'[^a-z0-9_]', '', new_col)
            
            if new_col and new_col not in cols:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {new_col} TEXT"))
                    flash(f"Column '{new_col}' added successfully.", "success")
                except Exception as e:
                    flash(f"Failed to add column: {e}", "danger")
                    
        elif action == "calculate_column":
            new_col = request.form.get("new_col", "").strip().lower()
            new_col = re.sub(r'[^a-z0-9_]', '', new_col)
            expression = request.form.get("expression", "").strip()

            if new_col and expression:
                if not re.match(r'^[\w\s\+\-\*\/\(\)\.]+$', expression):
                    flash("Invalid characters in formula. Only alphanumeric, spaces, and math operators are allowed.", "danger")
                else:
                    tokens = re.findall(r'[a-zA-Z_]\w*', expression)
                    invalid_tokens = [t for t in tokens if t.lower() not in cols]
                    
                    if invalid_tokens:
                        flash(f"Invalid columns in formula: {', '.join(invalid_tokens)}", "danger")
                    else:
                        try:
                            with engine.begin() as conn:
                                if new_col not in cols:
                                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {new_col} REAL"))
                                op_sql = f"UPDATE {table} SET {new_col} = {expression}"
                                conn.execute(text(op_sql))
                                conn.execute(text("DELETE FROM column_formulas WHERE table_name = :t AND column_name = :c"), {"t": table, "c": new_col})
                                conn.execute(text("INSERT INTO column_formulas (table_name, column_name, formula) VALUES (:t, :c, :f)"), {"t": table, "c": new_col, "f": expression})
                            flash(f"Calculated column '{new_col}' updated successfully.", "success")
                        except Exception as e:
                            flash(f"Failed to calculate column: {e}", "danger")
        return redirect(url_for("view_table", table=table))

    # GET request: fetch data
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        rows = df.to_dict(orient="records")
        
        formulas = {}
        with engine.connect() as conn:
            formula_rows = conn.execute(text("SELECT column_name, formula FROM column_formulas WHERE table_name = :t"), {"t": table}).mappings().all()
            for r in formula_rows:
                formulas[r["column_name"]] = r["formula"]
    except Exception as e:
        rows = []
        formulas = {}
        flash(f"Error loading data: {e}", "danger")

    return render_template("table_view.html", table=table, cols=cols, rows=rows, is_sample=False, current_user_id=current_user.id, formulas=formulas)

@app.route("/preview/<table>")
@login_required
def preview_table(table):
    schema = get_live_schema(engine, current_user.id)
    if table not in schema:
        flash("Table not found or access denied.", "danger")
        return redirect(url_for("manager"))

    cols = schema[table]["columns"]
    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        rows = df.to_dict(orient="records")
    except Exception as e:
        rows = []
        flash(f"Error loading data: {e}", "danger")

    return render_template("preview_table.html", table=table, cols=cols, rows=rows)

@app.route("/export_table_csv/<table>")
@login_required
def export_table_csv(table):
    schema = get_live_schema(engine, current_user.id)
    if table not in schema:
        flash("Table not found or access denied.", "danger")
        return redirect(url_for("manager"))

    try:
        with engine.connect() as conn:
            df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        
        export_path = APP_DIR / "db" / f"export_{table}.csv"
        df.to_csv(export_path, index=False)
        return send_file(export_path, as_attachment=True, download_name=f"{table}_data.csv")
    except Exception as e:
        flash(f"Error exporting data: {e}", "danger")
        return redirect(url_for("preview_table", table=table))
@app.route("/upload", methods=["POST"])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('manager'))
        
    file = request.files['file']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('manager'))
        
    if file:
        filename = secure_filename(file.filename)
        table_name_base = os.path.splitext(filename)[0].lower()
        table_name_base = re.sub(r'[^a-z0-9_]', '_', table_name_base)
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        try:
            if filename.endswith('.csv'):
                df = pd.read_csv(filepath)
            elif filename.endswith('.xlsx') or filename.endswith('.xls'):
                df = pd.read_excel(filepath)
            else:
                flash("Unsupported file format. Please upload CSV or Excel.", "danger")
                return redirect(url_for('manager'))
                
            # Clean column names
            df.columns = [re.sub(r'[^a-z0-9_]', '_', str(c).strip().lower()) for c in df.columns]
            
            actual_table_name = f"u{current_user.id}_{table_name_base}"
            
            # Use Pandas to_sql to generate and populate the table
            with engine.begin() as conn:
                df.to_sql(actual_table_name, conn, if_exists='replace', index=False)
                
            flash(f"Successfully uploaded and created table '{table_name_base}' with {len(df)} rows!", "success")
            return redirect(url_for('view_table', table=actual_table_name))
        except Exception as e:
            flash(f"Error processing file: {e}", "danger")
            
        return redirect(url_for('manager'))

@app.get("/export/csv")
def export_csv():
    path = APP_DIR / "db" / "last_result.csv"
    if not path.exists():
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="query_results.csv")

if __name__ == "__main__":
    app.run(debug=True)