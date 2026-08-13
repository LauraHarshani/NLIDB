from ml.predict_meaning import predict_meaning
from ml.meaning_normalizer import normalize_meaning
from ml.validator import validate_meaning
from ml.sql_builder import meaning_to_sql

schema = {
    "students": {
        "columns": ["student_id", "student_name", "marks", "joined_year", "department"],
        "text_cols": ["student_name", "department"],
        "num_cols": ["student_id", "marks", "joined_year"],
    },
    "employees": {
        "columns": ["employee_id", "employee_name", "salary", "department", "start_year"],
        "text_cols": ["employee_name", "department"],
        "num_cols": ["employee_id", "salary", "start_year"],
    },
}

queries = [
    "find Kasun mark",
    "show students with marks above 80",
    "top 3 students by marks",
    "count students",
    "employees in marketing",
]

for q in queries:
    raw_pred = predict_meaning(q)
    norm_pred = normalize_meaning(raw_pred, schema)
    ok, msg = validate_meaning(norm_pred, schema)

    print("\nQuery:", q)
    print("Raw:", raw_pred)
    print("Normalized:", norm_pred)
    print("Valid:", ok, msg)

    if ok:
        sql = meaning_to_sql(norm_pred, schema)
        print("SQL:", sql)