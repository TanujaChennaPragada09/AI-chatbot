from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import mysql.connector
import os, subprocess
from dotenv import load_dotenv
from docx import Document

# ---------- LOAD ENV ----------
load_dotenv()

app = Flask(__name__)
CORS(app)

# ---------- CONFIG ----------
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", 3306))
}

MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")  # FAST & STABLE
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------- DB ----------
def get_db():
    return mysql.connector.connect(**DB_CONFIG)

def init_db():
    db = get_db()
    cur = db.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100),
            role VARCHAR(10),
            message LONGTEXT,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(100),
            filename VARCHAR(255),
            content LONGTEXT,
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    db.commit()
    cur.close()
    db.close()

init_db()

# ---------- ROOT ----------
@app.route("/")
def home():
    return jsonify({
        "status": "running",
        "message": "AI backend is live 🚀"
    })

# ---------- LOGIN ----------
@app.route("/login", methods=["POST"])
def login():
    return jsonify({"status": "ok"})

# ---------- STREAMING CHAT ----------
@app.route("/chat-stream", methods=["POST"])
def chat_stream():
    data = request.json or {}
    msg = data.get("message", "").strip()
    user = data.get("username", "").strip()

    if not msg or not user:
        return jsonify({"response": "Invalid request"}), 400

    # Save USER message
    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO messages (username, role, message) VALUES (%s,'user',%s)",
        (user, msg)
    )
    db.commit()
    cur.close()
    db.close()

    # Load latest uploaded file (context)
    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT content FROM files
        WHERE username=%s
        ORDER BY id DESC
        LIMIT 1
    """, (user,))
    row = cur.fetchone()
    cur.close()
    db.close()

    file_context = row["content"] if row else ""

    prompt = f"""You are a helpful AI assistant.
Answer clearly and concisely.

User question:
{msg}

File content (if any):
{file_context}
"""

    def generate():
        process = subprocess.Popen(
            ["ollama", "run", MODEL],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0
        )

        process.stdin.write(prompt)
        process.stdin.close()

        full_reply = ""

        for line in process.stdout:
            full_reply += line
            yield line

        process.stdout.close()
        process.wait()

        # Save BOT reply
        if full_reply.strip():
            db2 = get_db()
            cur2 = db2.cursor()
            cur2.execute(
                "INSERT INTO messages (username, role, message) VALUES (%s,'bot',%s)",
                (user, full_reply.strip())
            )
            db2.commit()
            cur2.close()
            db2.close()

    return Response(generate(), mimetype="text/plain")

# ---------- HISTORY ----------
@app.route("/history")
def history():
    user = request.args.get("user", "")
    if not user:
        return jsonify([])

    db = get_db()
    cur = db.cursor(dictionary=True)
    cur.execute("""
        SELECT role, message, created
        FROM messages
        WHERE username=%s
        ORDER BY id DESC
        LIMIT 50
    """, (user,))
    data = cur.fetchall()
    cur.close()
    db.close()

    return jsonify(data)

# ---------- FILE UPLOAD ----------
@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400

    file = request.files["file"]
    user = request.form.get("username", "")

    if not user:
        return jsonify({"error": "No username"}), 400

    path = os.path.join(UPLOAD_DIR, file.filename)
    file.save(path)

    content = ""

    if file.filename.lower().endswith(".txt"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

    elif file.filename.lower().endswith(".docx"):
        doc = Document(path)
        content = "\n".join(p.text for p in doc.paragraphs)

    else:
        content = "Unsupported file format."

    db = get_db()
    cur = db.cursor()
    cur.execute(
        "INSERT INTO files (username, filename, content) VALUES (%s,%s,%s)",
        (user, file.filename, content[:15000])
    )
    db.commit()
    cur.close()
    db.close()

    return jsonify({"response": f"✅ File '{file.filename}' uploaded and analyzed"})

# ---------- CLEAR HISTORY ----------
@app.route("/clear-history", methods=["POST"])
def clear_history():
    data = request.json or {}
    user = data.get("username")

    if not user:
        return jsonify({"error": "No username"}), 400

    db = get_db()
    cur = db.cursor()
    cur.execute("DELETE FROM messages WHERE username=%s", (user,))
    cur.execute("DELETE FROM files WHERE username=%s", (user,))
    db.commit()
    cur.close()
    db.close()

    return jsonify({"status": "cleared"})

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, threaded=True)
