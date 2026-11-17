import sqlite3

conn = sqlite3.connect("exam.db")
cur = conn.cursor()

try:
    cur.execute("INSERT INTO students (username, password) VALUES ('student', 'password123')")
    conn.commit()
    print("✅ Success! You can now log in with student / password123")
except sqlite3.IntegrityError:
    print("⚠️ The user 'student' already exists.")
    
conn.close()