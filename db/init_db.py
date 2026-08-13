import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "sample.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Drop if exists (dev-friendly)
    cur.executescript("""
    DROP TABLE IF EXISTS employees;
    DROP TABLE IF EXISTS students;
    DROP TABLE IF EXISTS users;
    """)

    cur.executescript("""
    CREATE TABLE employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        department TEXT NOT NULL,
        start_year INTEGER NOT NULL,
        salary REAL NOT NULL
    );

    CREATE TABLE students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        joined_year INTEGER NOT NULL,
        marks REAL NOT NULL
    );

    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    employees = [
        ("Nimal", "marketing", 2022, 180000),
        ("Kamal", "finance", 2020, 220000),
        ("Sahan", "marketing", 2021, 200000),
        ("Amaya", "it", 2023, 250000),
    ]
    students = [
        ("Shehani", 2021, 82),
        ("Kasun", 2019, 91),
        ("Tharindu", 2022, 74),
        ("Nethmi", 2023, 88),


    ]

    cur.executemany("INSERT INTO employees (name, department, start_year, salary) VALUES (?,?,?,?)", employees)
    cur.executemany("INSERT INTO students (name, joined_year, marks) VALUES (?,?,?)", students)

    conn.commit()
    conn.close()
    print(f"Database created: {DB_PATH}")

if __name__ == "__main__":
    main()