SCHEMA = {
    "employees": {
        "columns": ["id", "name", "department", "start_year", "salary"],
        "keywords": ["employee", "employees", "staff", "worker", "workers"],
    },
    "students": {
        "columns": ["id", "name", "joined_year", "marks"],
        "keywords": ["student", "students", "undergraduate", "learners"],
    },
}

# Simple mapping from natural phrases -> columns/operators
COLUMN_SYNONYMS = {
    "department": ["department", "dept"],
    "start_year": ["start", "started", "start year", "joined work", "started work"],
    "joined_year": ["joined", "joined year", "enrolled"],
    "marks": ["marks", "score", "scored", "grade", "result"],
    "salary": ["salary", "pay", "income"],
}

DEPT_VALUES = ["marketing", "finance", "it", "hr", "sales"]