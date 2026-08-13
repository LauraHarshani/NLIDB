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

examples = [
    {
        "intent": "select",
        "target_table": "students",
        "target_columns": "*",
        "filters": "marks > 80",
        "joins": "",
        "group_by": "",
        "sort_column": "",
        "sort_order": "",
        "limit_value": "",
        "aggregate": "",
    },
    {
        "intent": "count",
        "target_table": "students",
        "target_columns": "*",
        "filters": "",
        "joins": "",
        "group_by": "",
        "sort_column": "",
        "sort_order": "",
        "limit_value": "",
        "aggregate": "count",
    },
    {
        "intent": "select",
        "target_table": "students",
        "target_columns": "*",
        "filters": "",
        "joins": "",
        "group_by": "",
        "sort_column": "marks",
        "sort_order": "desc",
        "limit_value": "3",
        "aggregate": "",
    },
    {
        "intent": "select",
        "target_table": "employees",
        "target_columns": "*",
        "filters": "department = 'marketing'",
        "joins": "",
        "group_by": "",
        "sort_column": "",
        "sort_order": "",
        "limit_value": "",
        "aggregate": "",
    },
]

for i, pred in enumerate(examples, 1):
    sql = meaning_to_sql(pred, schema)
    print(f"{i}. {sql}")