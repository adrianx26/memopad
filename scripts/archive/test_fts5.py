import sqlite3
import sys

print(f"Python version: {sys.version}")
print(f"SQLite version: {sqlite3.sqlite_version}")

try:
    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.execute("CREATE VIRTUAL TABLE email USING fts5(sender, title, body);")
    print("FTS5 table created successfully.")
    cur.execute("INSERT INTO email(sender, title, body) VALUES ('adrian', 'hello', 'world');")
    conn.commit()
    print("Insertion successful.")
    for row in cur.execute("SELECT * FROM email WHERE email MATCH 'world'"):
        print(f"Match found: {row}")
except Exception as e:
    print(f"Error: {e}")
