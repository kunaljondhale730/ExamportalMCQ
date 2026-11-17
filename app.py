from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import random
import time
import string
import ast
import pandas as pd

app = Flask(__name__)
app.secret_key = "exam-portal-secret-2025"
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "exam.db")

# ---------------------------------------------------------
# CHANGE THIS TO CHANGE THE MASTER ADMIN PASSWORD
ADMIN_PASSWORD = "admin123" 
# ---------------------------------------------------------

DEFAULT_QUESTIONS_PER_EXAM = 25
DEFAULT_DURATION_MIN = 30


# ---------- DATABASE ----------
def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT DEFAULT '',
            question_count INTEGER DEFAULT 25,
            duration_minutes INTEGER DEFAULT 30,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_name TEXT NOT NULL,
            question TEXT NOT NULL,
            option_a TEXT NOT NULL,
            option_b TEXT NOT NULL,
            option_c TEXT NOT NULL,
            option_d TEXT NOT NULL,
            correct_option TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT NOT NULL,
            set_name TEXT NOT NULL,
            question_ids TEXT NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            percentage REAL NOT NULL,
            answers TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT,
            assigned_set TEXT,
            role TEXT DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cur.execute("ALTER TABLE students ADD COLUMN role TEXT DEFAULT 'user'")
    except sqlite3.OperationalError:
        pass 

    cur.execute("""
        DELETE FROM questions 
        WHERE id NOT IN (
            SELECT MIN(id) 
            FROM questions 
            GROUP BY set_name, LOWER(TRIM(question))
        )
    """)

    cur.execute("INSERT OR IGNORE INTO students (username, password, name, role) VALUES ('admin', 'admin123', 'System Admin', 'admin')")
    cur.execute("INSERT OR IGNORE INTO students (username, password, name, role) VALUES ('student', 'password123', 'Test Student', 'user')")
    
    conn.commit()
    conn.close()


def get_all_sets():
    conn = get_db()
    rows = conn.execute("""
        SELECT s.name, s.description, s.question_count, s.duration_minutes,
               (SELECT COUNT(DISTINCT LOWER(TRIM(question))) FROM questions q WHERE q.set_name = s.name) AS qcount
        FROM sets s ORDER BY s.name
    """).fetchall()
    conn.close()
    return rows


def create_exam(student_username):
    conn = get_db()
    student = conn.execute("SELECT assigned_set FROM students WHERE username = ?", (student_username,)).fetchone()
    
    if student and student["assigned_set"]:
        # Student already has a set assigned (either by Admin or from a previous exam)
        set_name = student["assigned_set"]
        set_info = conn.execute("SELECT question_count, duration_minutes FROM sets WHERE name = ?", (set_name,)).fetchone()
        if not set_info:
            conn.close()
            return None, [], 0
    else:
        # Student has NO set assigned. Pick a random one and SAVE it to the database.
        sets = get_all_sets()
        eligible = [s for s in sets if s["qcount"] > 0]
        if not eligible: 
            conn.close()
            return None, [], 0
        
        chosen_set = random.choice(eligible)
        set_name = chosen_set["name"]
        set_info = chosen_set
        
        # CRITICAL FIX: Save this random set to the student's profile permanently
        conn.execute("UPDATE students SET assigned_set = ? WHERE username = ?", (set_name, student_username))
        conn.commit()

    per_exam = set_info["question_count"] or DEFAULT_QUESTIONS_PER_EXAM
    duration = set_info["duration_minutes"] or DEFAULT_DURATION_MIN

    rows = conn.execute("""
        SELECT MIN(id) as id 
        FROM questions 
        WHERE set_name = ? 
        GROUP BY LOWER(TRIM(question))
    """, (set_name,)).fetchall()
    conn.close()
    
    all_ids = [r["id"] for r in rows]
    if not all_ids: return set_name, [], duration
    
    take = min(per_exam, len(all_ids))
    chosen = random.sample(all_ids, take)
    return set_name, chosen, duration


# ---------- AUTHENTICATION & STUDENT ROUTES ----------
@app.route("/")
def dashboard():
    if "student_name" not in session or not session.get("student_name"):
        return redirect(url_for("login"))
        
    if session.get("role") == "admin" or session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    conn = get_db()
    
    user_info = conn.execute("SELECT name FROM students WHERE username = ?", (session["student_name"],)).fetchone()
    full_name = user_info["name"] if user_info and user_info["name"] else session["student_name"]

    attempts = conn.execute("SELECT * FROM attempts WHERE student_name = ? ORDER BY id DESC LIMIT 5", (session["student_name"],)).fetchall()
    total_results = conn.execute("SELECT COUNT(*) FROM attempts WHERE student_name = ?", (session["student_name"],)).fetchone()[0]
    avg_row = conn.execute("SELECT AVG(percentage) FROM attempts WHERE student_name = ?", (session["student_name"],)).fetchone()
    avg_score = round(avg_row[0], 1) if avg_row[0] is not None else 0
    conn.close()

    return render_template("dashboard.html",
                           attempts=attempts, total_results=total_results, avg_score=avg_score,
                           student_name=session.get("student_name"),
                           full_name=full_name)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        if username == 'admin' and password == ADMIN_PASSWORD:
            session["student_name"] = "admin"
            session["role"] = "admin"
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))

        conn = get_db()
        user = conn.execute("SELECT * FROM students WHERE username = ? AND password = ?", (username, password)).fetchone()
        conn.close()
        
        if user:
            session["student_name"] = user["username"]
            session["role"] = user["role"]
            
            if user["role"] == "admin":
                session["is_admin"] = True
                return redirect(url_for("admin_dashboard"))
            
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid Username or Password")
            
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/start", methods=["POST"])
def start():
    if "student_name" not in session: 
        return redirect(url_for("login"))
        
    if session.get("role") == "admin" or session.get("is_admin"):
        return "Admins cannot take exams. Please log in as a student.", 403
    
    set_name, chosen_ids, duration = create_exam(session["student_name"])
    if not set_name: return "No questions available or assigned set is empty.", 400

    session["set_name"] = set_name
    session["question_ids"] = chosen_ids
    session["duration_minutes"] = duration
    session["start_time"] = time.time()
    return redirect(url_for("exam"))


@app.route("/exam")
def exam():
    if "question_ids" not in session: return redirect(url_for("dashboard"))
    chosen_ids = session["question_ids"]
    duration = session.get("duration_minutes", DEFAULT_DURATION_MIN)
    start_time = session.get("start_time", time.time())
    remaining = max(0, int(duration * 60 - (time.time() - start_time)))
    if remaining <= 0: return redirect(url_for("submit", auto="1"))

    placeholders = ",".join("?" * len(chosen_ids))
    conn = get_db()
    rows = conn.execute(f"SELECT * FROM questions WHERE id IN ({placeholders})", chosen_ids).fetchall()
    conn.close()
    qmap = {r["id"]: r for r in rows}
    questions = [qmap[i] for i in chosen_ids if i in qmap]

    return render_template("exam.html", questions=questions, set_name=session["set_name"],
                           student_name=session["student_name"], duration_minutes=duration,
                           remaining_seconds=remaining)


@app.route("/submit", methods=["GET", "POST"])
def submit():
    if "question_ids" not in session: return redirect(url_for("dashboard"))
    chosen_ids = session["question_ids"]
    set_name = session["set_name"]
    student_name = session["student_name"]
    duration = session.get("duration_minutes", DEFAULT_DURATION_MIN)
    start_time = session.get("start_time", time.time())
    time_up = (time.time() - start_time) >= duration * 60

    placeholders = ",".join("?" * len(chosen_ids))
    conn = get_db()
    rows = conn.execute(f"SELECT * FROM questions WHERE id IN ({placeholders})", chosen_ids).fetchall()
    qmap = {r["id"]: r for r in rows}

    score = 0
    review = []
    answer_log = {}

    for qid in chosen_ids:
        q = qmap[qid]
        chosen = request.form.get(f"q_{qid}", "") if request.method == "POST" else ""
        correct = q["correct_option"].upper()
        if chosen and chosen.upper() == correct: score += 1
        
        option_map = {"A": q["option_a"], "B": q["option_b"], "C": q["option_c"], "D": q["option_d"]}
        
        review.append({
            "question": q["question"],
            "option_a": q["option_a"],
            "option_b": q["option_b"],
            "option_c": q["option_c"],
            "option_d": q["option_d"],
            "chosen": option_map.get(chosen, "Not answered") if chosen else "Not answered",
            "correct": option_map[correct],
            "is_correct": chosen.upper() == correct if chosen else False,
        })
        answer_log[str(qid)] = chosen

    total = len(chosen_ids)
    percentage = round(score * 100 / total, 1) if total else 0

    conn.execute("INSERT INTO attempts (student_name, set_name, question_ids, score, total, percentage, answers) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (student_name, set_name, ",".join(map(str, chosen_ids)), score, total, percentage, str(answer_log)))
    conn.commit()
    conn.close()

    session.pop("question_ids", None)
    session.pop("set_name", None)
    session.pop("duration_minutes", None)
    session.pop("start_time", None)

    return render_template("result.html", score=score, total=total, percentage=percentage,
                           review=review, student_name=student_name, set_name=set_name, time_up=time_up)


# ---------- ADMIN ROUTES ----------
@app.route("/admin")
def admin():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    return redirect(url_for("login"))


@app.route("/admin/dashboard")
def admin_dashboard():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    conn = get_db()
    tq = conn.execute("SELECT COUNT(DISTINCT LOWER(TRIM(question))) FROM questions").fetchone()[0]
    ta = conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    student_count = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
    conn.close()
    
    return render_template("admin_dashboard.html", page="dashboard", sets=get_all_sets(), 
                           total_questions=tq, total_attempts=ta, student_count=student_count)


@app.route("/admin/students")
def admin_students():
    if not session.get("is_admin"): return redirect(url_for("login"))
    conn = get_db()
    students = conn.execute("SELECT * FROM students ORDER BY id DESC").fetchall()
    conn.close()
    
    return render_template("admin_students.html", page="students", sets=get_all_sets(), students=students)


@app.route("/admin/add_student", methods=["POST"])
def admin_add_student():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    emp_id = request.form.get("username", "").strip()
    name = request.form.get("name", "").strip()
    assigned_set = request.form.get("assigned_set", "").strip()
    role = request.form.get("role", "user").strip()
    
    if emp_id and name:
        characters = string.ascii_letters + string.digits
        generated_password = ''.join(random.choice(characters) for i in range(12))
        
        conn = get_db()
        try:
            conn.execute("INSERT INTO students (username, password, name, assigned_set, role) VALUES (?, ?, ?, ?, ?)", 
                         (emp_id, generated_password, name, assigned_set, role))
            conn.commit()
        except sqlite3.IntegrityError:
            pass
        conn.close()
        
    return redirect(url_for("admin_students"))


@app.route("/admin/delete_student/<username>", methods=["POST"])
def delete_student(username):
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    conn = get_db()
    conn.execute("DELETE FROM students WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    
    return redirect(url_for("admin_students"))


@app.route("/admin/update_user_info", methods=["POST"])
def update_user_info():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    emp_id = request.form.get("username")
    new_set = request.form.get("assigned_set")
    new_role = request.form.get("role")
    new_password = request.form.get("new_password")
    
    conn = get_db()
    conn.execute("UPDATE students SET assigned_set = ?, role = ?, password = ? WHERE username = ?", 
                 (new_set, new_role, new_password, emp_id))
    conn.commit()
    conn.close()
    
    return redirect(url_for("admin_students"))


@app.route("/admin/sets")
def admin_sets():
    if not session.get("is_admin"): return redirect(url_for("login"))
    return render_template("admin_sets.html", page="sets", sets=get_all_sets())


@app.route("/admin/upload_excel", methods=["POST"])
def upload_excel():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    set_name = request.form.get("set_name", "").strip().upper()
    desc = request.form.get("set_description", "").strip()
    try: qcount = max(1, int(request.form.get("question_count", "25")))
    except: qcount = 25
    try: duration = max(1, int(request.form.get("duration_minutes", "30")))
    except: duration = 30
    
    file = request.files.get("excel_file")
    
    if file and set_name:
        try:
            conn = get_db()
            conn.execute("INSERT OR IGNORE INTO sets (name, description, question_count, duration_minutes) VALUES (?, ?, ?, ?)", 
                         (set_name, desc, qcount, duration))
            
            filename = file.filename.lower()
            if filename.endswith(".csv"):
                df = pd.read_csv(file)
            else:
                df = pd.read_excel(file)
            
            df.columns = [c.strip() for c in df.columns]
            added_count = 0
            skipped_count = 0
            
            for index, row in df.iterrows():
                q_text = str(row['Question']).strip()
                
                existing = conn.execute("SELECT id FROM questions WHERE LOWER(TRIM(question)) = LOWER(TRIM(?))", (q_text,)).fetchone()
                
                if existing:
                    skipped_count += 1
                    continue
                
                conn.execute("""
                    INSERT OR IGNORE INTO questions (set_name, question, option_a, option_b, option_c, option_d, correct_option) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (set_name, q_text, str(row['Option A']).strip(), str(row['Option B']).strip(), 
                      str(row['Option C']).strip(), str(row['Option D']).strip(), str(row['Correct Option']).strip().upper()))
                
                added_count += 1
            
            conn.commit()
            conn.close()
            print(f"✅ Upload Success: Added {added_count} questions. Skipped {skipped_count} duplicates.")
            
        except Exception as e:
            print(f"Error uploading file: {e}")
            conn = get_db()
            conn.execute("DELETE FROM sets WHERE name = ?", (set_name,))
            conn.commit()
            conn.close()
            
    return redirect(url_for("admin_sets"))


@app.route("/admin/results")
def admin_results():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    filter_set = request.args.get("set", "").strip()
    conn = get_db()
    
    if filter_set:
        attempts = conn.execute("""
            SELECT a.*, s.name as full_name 
            FROM attempts a 
            LEFT JOIN students s ON a.student_name = s.username 
            WHERE a.set_name = ? 
            ORDER BY a.id DESC
        """, (filter_set,)).fetchall()
    else:
        attempts = conn.execute("""
            SELECT a.*, s.name as full_name 
            FROM attempts a 
            LEFT JOIN students s ON a.student_name = s.username 
            ORDER BY a.id DESC
        """).fetchall()

    detailed = []
    for a in attempts:
        ids = [int(x) for x in a["question_ids"].split(",")]
        placeholders = ",".join("?" * len(ids))
        
        qs = conn.execute(f"SELECT id, question, option_a, option_b, option_c, option_d, correct_option FROM questions WHERE id IN ({placeholders})", ids).fetchall()
        qmap = {q["id"]: q for q in qs}
        
        try:
            student_answers = ast.literal_eval(a["answers"]) if a["answers"] else {}
        except Exception as e:
            print(f"Error parsing answers: {e}")
            student_answers = {}

        items = []
        for qid in ids:
            if qid in qmap:
                items.append({
                    "id": qid, 
                    "question": qmap[qid]["question"], 
                    "option_a": qmap[qid]["option_a"],
                    "option_b": qmap[qid]["option_b"],
                    "option_c": qmap[qid]["option_c"],
                    "option_d": qmap[qid]["option_d"],
                    "correct": qmap[qid]["correct_option"],
                    "selected_answer": student_answers.get(str(qid), "") 
                })
                
        detailed.append({
            "id": a["id"], 
            "student_id": a["student_name"], 
            "full_name": a["full_name"] or "Deleted User",
            "set_name": a["set_name"],
            "score": a["score"], 
            "total": a["total"], 
            "percentage": a["percentage"],
            "created_at": a["created_at"], 
            "questions": items,
        })
    conn.close()
    
    return render_template("admin_results.html", page="results", sets=get_all_sets(), attempts=detailed, selected_set=filter_set)


@app.route("/admin/create_set", methods=["POST"])
def create_set():
    if not session.get("is_admin"): return redirect(url_for("login"))
    name = request.form.get("set_name", "").strip().upper()
    desc = request.form.get("set_description", "").strip()
    try: qcount = max(1, int(request.form.get("question_count", "25")))
    except: qcount = 25
    try: duration = max(1, int(request.form.get("duration_minutes", "30")))
    except: duration = 30

    if name:
        conn = get_db()
        try:
            conn.execute("INSERT INTO sets (name, description, question_count, duration_minutes) VALUES (?, ?, ?, ?)", (name, desc, qcount, duration))
            conn.commit()
        except sqlite3.IntegrityError: pass
        conn.close()
    return redirect(url_for("admin_sets"))


@app.route("/admin/delete_set", methods=["POST"])
def delete_set():
    if not session.get("is_admin"): return redirect(url_for("login"))
    name = request.form.get("set_name", "").strip()
    if name:
        conn = get_db()
        conn.execute("DELETE FROM sets WHERE name = ?", (name,))
        conn.commit()
        conn.close()
    return redirect(url_for("admin_sets"))


@app.route("/admin/add_question", methods=["POST"])
def add_question():
    if not session.get("is_admin"): return redirect(url_for("login"))
    set_name = request.form.get("set_name", "").strip().upper()
    q = request.form.get("question", "").strip()
    a, b, c, d = [request.form.get(f"option_{x}", "").strip() for x in "abcd"]
    correct = request.form.get("correct_option", "").strip().upper()

    if set_name and correct in ("A", "B", "C", "D") and q and a and b and c and d:
        conn = get_db()
        
        existing = conn.execute("SELECT id FROM questions WHERE LOWER(TRIM(question)) = LOWER(TRIM(?))", (q,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO questions (set_name, question, option_a, option_b, option_c, option_d, correct_option) VALUES (?, ?, ?, ?, ?, ?, ?)", (set_name, q, a, b, c, d, correct))
            conn.commit()
        else:
            print(f"🛑 BLOCKED: Duplicate question '{q}' was not added.")
            
        conn.close()
        
    return redirect(url_for("admin_questions", set=set_name, success=1))


@app.route("/admin/delete_question/<int:qid>", methods=["POST"])
def delete_question(qid):
    if not session.get("is_admin"): return redirect(url_for("login"))
    print(f"🛑 BLOCKED: Attempted to delete question ID {qid}. Deletion is disabled.")
    return redirect(request.referrer or url_for("admin_sets"))


@app.route("/admin/questions")
def admin_questions():
    if not session.get("is_admin"): return redirect(url_for("login"))
    filter_set = request.args.get("set", "").strip()
    conn = get_db()
    
    if filter_set:
        questions = conn.execute("""
            SELECT MIN(id) as id, 
                   GROUP_CONCAT(DISTINCT set_name) as set_name, 
                   question, option_a, option_b, option_c, option_d, correct_option
            FROM questions 
            WHERE set_name = ?
            GROUP BY LOWER(TRIM(question))
            ORDER BY id
        """, (filter_set,)).fetchall()
    else:
        questions = conn.execute("""
            SELECT MIN(id) as id, 
                   GROUP_CONCAT(DISTINCT set_name) as set_name, 
                   question, option_a, option_b, option_c, option_d, correct_option
            FROM questions 
            GROUP BY LOWER(TRIM(question))
            ORDER BY set_name, id
        """).fetchall()
        
    conn.close()
    return render_template("admin_questions.html", page="questions", questions=questions, sets=get_all_sets(), selected_set=filter_set)


@app.route("/admin/combine_questions")
def admin_combine_questions():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    conn = get_db()
    questions = conn.execute("""
        SELECT MIN(id) as id, 
               GROUP_CONCAT(DISTINCT set_name) as set_name, 
               question, option_a, option_b, option_c, option_d, correct_option
        FROM questions 
        GROUP BY LOWER(TRIM(question))
        ORDER BY set_name, id
    """).fetchall()
    conn.close()
    
    return render_template("admin_combine.html", page="combine", questions=questions)


@app.route("/admin/create_combined_set", methods=["POST"])
def create_combined_set():
    if not session.get("is_admin"): return redirect(url_for("login"))
    
    set_name = request.form.get("set_name", "").strip().upper()
    
    try: q_count = int(request.form.get("question_count", 25))
    except: q_count = 25
    try: duration = int(request.form.get("duration_minutes", 30))
    except: duration = 30
    
    selected_ids = request.form.getlist("question_ids")
    
    if set_name and selected_ids:
        conn = get_db()
        try:
            conn.execute("INSERT INTO sets (name, description, question_count, duration_minutes) VALUES (?, ?, ?, ?)", 
                         (set_name, "Combined from existing questions", q_count, duration))
            
            unique_questions = []
            seen_texts = set()
            
            for qid in selected_ids:
                orig_q = conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone()
                if orig_q:
                    q_text_normalized = orig_q["question"].strip().lower()
                    if q_text_normalized not in seen_texts:
                        unique_questions.append(orig_q)
                        seen_texts.add(q_text_normalized)
            
            if len(unique_questions) > q_count:
                unique_questions = random.sample(unique_questions, q_count)
                
            added_count = 0
            for orig_q in unique_questions:
                conn.execute("""
                    INSERT INTO questions (set_name, question, option_a, option_b, option_c, option_d, correct_option) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (set_name, orig_q["question"], orig_q["option_a"], orig_q["option_b"], 
                      orig_q["option_c"], orig_q["option_d"], orig_q["correct_option"]))
                added_count += 1
            
            conn.commit()
            print(f"✅ Combined Set '{set_name}' created with {added_count} unique questions.")
        except sqlite3.IntegrityError:
            print(f"🛑 ERROR: The set name '{set_name}' already exists. Please use a different name.")
        except Exception as e:
            print(f"🛑 ERROR: {e}")
        conn.close()
        
    return redirect(url_for("admin_sets"))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)