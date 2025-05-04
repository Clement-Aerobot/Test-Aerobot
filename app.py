import psycopg2
import psycopg2.extras
import os
import openai
import time
from flask import Flask, render_template, request, redirect, url_for, session, flash
from functools import wraps
from datetime import datetime
from math import ceil
import csv
import random

# --- CONFIG ---
SECRET_KEY = "super-secret-change-me"
app = Flask(__name__)
app.secret_key = SECRET_KEY

# Chargement des sujets une fois au démarrage
SUJETS_PATH = "sujets.csv"
SUJETS = []
with open(SUJETS_PATH, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f, delimiter=';')
    for row in reader:
        SUJETS.append(row)

@app.route("/api/proposer_sujet", methods=["GET"])
def api_proposer_sujet():
    if not SUJETS:
        return {"ok": False, "error": "Aucun sujet disponible"}, 404
    sujet = random.choice(SUJETS)
    return {
        "ok": True,
        "module": sujet.get("MODULE", ""),
        "matiere": sujet.get("MATIERE", ""),
        "chapitre": sujet.get("CHAPITRE", ""),
        "titre": sujet.get("TITRE DU PARAGRAPHE", ""),
    }

# --- UTILS DB ---
def get_db():
    return psycopg2.connect(
        host="dpg-d0bp3v95pdvs73ctvecg-a",
        dbname="test_aerobot_db",
        user="test_aerobot_db_user",
        password="ABq8zFmJODZgCEgGoCxF5GTfNFoXrwCk"
    )

def get_user_by_username(username):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT id, username, password, is_admin FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row["id"], "username": row["username"], "password": row["password"], "is_admin": bool(row["is_admin"])
        }
    return None

def get_user_by_id(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT id, username, is_admin FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"])
        }
    return None

# --- DÉCORATEURS ---
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if not session.get("is_admin"):
            flash("Accès réservé à l'admin.", "error")
            return redirect(url_for("home_testeur"))
        return f(*args, **kwargs)
    return decorated

# --- ROUTES ---

@app.route("/testeur/session/<int:session_id>/close", methods=["POST"])
@login_required
def testeur_close_session(session_id):
    user_id = session["user_id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT ended_at FROM sessions WHERE id=%s AND user_id=%s", (session_id, user_id))
    row = cur.fetchone()
    if not row:
        conn.close()
        flash("Session introuvable.", "error")
        return redirect(url_for("testeur_dashboard"))
    if row[0]:
        conn.close()
        flash("Session déjà clôturée.", "error")
        return redirect(url_for("testeur_session_detail", session_id=session_id))
    cur.execute("UPDATE sessions SET ended_at=%s WHERE id=%s", (datetime.utcnow().isoformat(), session_id))
    conn.commit()
    conn.close()
    flash("Session clôturée.", "success")
    return redirect(url_for('testeur_session_detail', session_id=session_id))

@app.route("/", methods=["GET"])
def index():
    """Redirige si déjà connecté !"""
    if session.get("user_id"):
        if session.get("is_admin"):
            return redirect(url_for("home_admin"))
        else:
            return redirect(url_for("home_testeur"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"]
        user = get_user_by_username(username)
        if user and user["password"] == password:
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["is_admin"] = user["is_admin"]
            # Dernier login en base (optionnel)
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE users SET last_login=%s WHERE id=%s", (datetime.utcnow().isoformat(), user["id"]))
            conn.commit()
            conn.close()
            if user["is_admin"]:
                return redirect(url_for("home_admin"))
            else:
                return redirect(url_for("home_testeur"))
        else:
            error = "Identifiants incorrects."
    return render_template("login.html", error=error)

@app.route("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/home_testeur")
@login_required
def home_testeur():
    if session.get("is_admin"):
        return redirect(url_for("home_admin"))
    user = get_user_by_id(session["user_id"])
    return render_template("home_testeur.html", user=user)

# ==== GESTION TESTEURS (ADMIN) ====

@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    page = int(request.args.get("page", 1))
    PER_PAGE = 10
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users WHERE is_admin=0")
    total = cur.fetchone()[0]
    nb_pages = int(ceil(total / PER_PAGE)) if total else 1
    offset = (page - 1) * PER_PAGE
    cur.execute(
        "SELECT id, username, created_at, last_login FROM users WHERE is_admin=0 ORDER BY id LIMIT %s OFFSET %s",
        (PER_PAGE, offset)
    )
    users = cur.fetchall()
    conn.close()
    return render_template(
        "admin_users.html",
        users=users,
        page=page,
        nb_pages=nb_pages
    )

@app.route("/admin/users/add", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users_add():
    password_generated = None
    username_entered = ""
    if request.method == "POST":
        username = request.form["username"].strip()
        username_entered = username
        if not username:
            flash("Nom d'utilisateur requis", "error")
        else:
            from random import choices
            import string
            password_generated = ''.join(choices(string.ascii_letters + string.digits, k=8))
            now = datetime.utcnow().isoformat()
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO users (username, password, is_admin, created_at) VALUES (%s, %s, 0, %s)",
                    (username, password_generated, now)
                )
                conn.commit()
                conn.close()
                flash("Testeur créé avec succès !")
            except psycopg2.IntegrityError:
                flash("Ce nom d'utilisateur existe déjà.", "error")
                password_generated = None
    return render_template("admin_users_add.html", password_generated=password_generated, username_entered=username_entered)

@app.route("/admin/users/delete/<int:user_id>", methods=["POST"])
@login_required
@admin_required
def admin_users_delete(user_id):
    if request.form.get("confirm") == "yes":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id=%s AND is_admin=0", (user_id,))
        conn.commit()
        conn.close()
        flash("Testeur supprimé.", "success")
    else:
        flash("Suppression annulée.", "error")
    return redirect(url_for("admin_users"))

# ==== DASHBOARD TESTEUR & SESSIONS ====

@app.route("/testeur/dashboard")
@login_required
def testeur_dashboard():
    if session.get("is_admin"):
        return redirect(url_for("home_admin"))
    user_id = session["user_id"]
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, started_at, ended_at FROM sessions WHERE user_id=%s ORDER BY started_at DESC",
        (user_id,)
    )
    rows = cur.fetchall()
    conn.close()

    class Sess:
        def __init__(self, id, started_at, ended_at):
            self.id = id
            self.started_at = started_at
            self.ended_at = ended_at

    sessions = [Sess(*row) for row in rows]

    return render_template("testeur_dashboard.html", sessions=sessions)

@app.route("/testeur/start_session", methods=["POST"])
@login_required
def testeur_start_session():
    if session.get("is_admin"):
        return redirect(url_for("home_admin"))
    user_id = session["user_id"]

    # Vérifier qu'il n'y a pas déjà une session en cours
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM sessions WHERE user_id=%s AND ended_at IS NULL", (user_id,))
    ongoing = cur.fetchone()
    if ongoing:
        flash("Vous avez déjà une session en cours ! Terminez-la avant d'en commencer une nouvelle.", "error")
        conn.close()
        # 👉 Redirige directement sur la session en cours
        return redirect(url_for("testeur_session_detail", session_id=ongoing[0]))

    started_at = datetime.utcnow().isoformat()
    cur.execute("INSERT INTO sessions (user_id, started_at) VALUES (%s, %s)", (user_id, started_at))
    conn.commit()
    # Récupérer l'id de la session nouvellement créée
    cur.execute("SELECT currval(pg_get_serial_sequence('sessions','id'))")
    session_id = cur.fetchone()[0]
    conn.close()
    flash("Nouvelle session de test démarrée !", "success")
    # 👉 Redirige immédiatement sur la nouvelle session
    return redirect(url_for("testeur_session_detail", session_id=session_id))

@app.route("/testeur/session/<int:session_id>", methods=["GET", "POST"])
@login_required
def testeur_session_detail(session_id):
    user_id = session["user_id"]

    # Charger la session
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, started_at, ended_at FROM sessions WHERE id=%s AND user_id=%s", (session_id, user_id))
    row = cur.fetchone()

    if not row:
        conn.close()
        flash("Session introuvable ou accès refusé.", "error")
        return redirect(url_for("testeur_dashboard"))

    session_data = {
        "id": row[0],
        "user_id": row[1],
        "started_at": row[2],
        "ended_at": row[3]
    }

    # Ajout d'une question ?
    if request.method == "POST" and not session_data["ended_at"]:
        question_text = request.form["question_text"].strip()
        answer_text = request.form["answer_text"].strip()
        appreciation = request.form.get("appreciation", "").strip()
        note = request.form.get("note", "").strip()
        is_correct = 1 if request.form.get("is_correct") == "1" else 0
        rag_used = 1 if request.form.get("rag_used") == "1" else 0
        try:
            note_int = int(note)
            if note_int < 0 or note_int > 10:
                raise ValueError()
        except Exception:
            note_int = None
        now = datetime.utcnow().isoformat()

        if not question_text or not answer_text or note_int is None:
            flash("Tous les champs sont requis + note de 0 à 10.", "error")
        else:
            cur.execute("""
                INSERT INTO questions 
                  (session_id, question_text, answer_text, appreciation, note, is_correct, rag_used, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (session_id, question_text, answer_text, appreciation, note_int, is_correct, rag_used, now))
            conn.commit()
            flash("Question enregistrée.", "success")

    # Récupérer les questions
    cur.execute("SELECT id, question_text, answer_text, appreciation, note, is_correct, rag_used, created_at FROM questions WHERE session_id=%s ORDER BY id ASC", (session_id,))
    questions = cur.fetchall()
    conn.close()

    return render_template(
        "testeur_session_detail.html",
        session_data=session_data,
        questions=questions
    )

@app.route("/admin/questions")
@login_required
@admin_required
def admin_questions():
    # Recherche toutes les questions + joint infos testeur, session
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT q.id, u.username, s.id, q.created_at, q.question_text, q.answer_text, q.note, q.appreciation, 
               q.is_correct, q.rag_used
        FROM questions q
        JOIN sessions s ON q.session_id = s.id
        JOIN users u ON s.user_id = u.id
        ORDER BY q.created_at DESC
        LIMIT 2000
    """)
    questions = cur.fetchall()
    conn.close()

    return render_template("admin_questions.html", questions=questions)

@app.route("/admin/sessions")
@login_required
@admin_required
def admin_sessions():
    import datetime
    conn = get_db()
    cur = conn.cursor()
    # On récupère toutes les sessions + username du testeur
    cur.execute("""
        SELECT
            s.id, u.username, s.started_at, s.ended_at
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        ORDER BY s.started_at DESC
        LIMIT 500
    """)
    sessions = []
    for sess_id, username, started_at, ended_at in cur.fetchall():
        # Récupérer stats sur cette session
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT COUNT(*), 
                   AVG(note), 
                   MIN(note), 
                   MAX(note),
                   STRING_AGG(note::text, ','),
                   SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN rag_used=1 THEN 1 ELSE 0 END)
            FROM questions 
            WHERE session_id=%s
        """, (sess_id,))
        (
            nb_tot,
            moyenne,
            mini,
            maxi,
            notes_concat,
            nb_correct,
            nb_rag
        ) = cur2.fetchone()
        
        # Calcul médiane manuelle en Python
        if notes_concat:
            notes_list = list(map(int, notes_concat.split(',')))
            notes_list.sort()
            n = len(notes_list)
            if n % 2 == 1:
                mediane = notes_list[n // 2]
            else:
                mediane = (notes_list[n // 2 - 1] + notes_list[n // 2]) / 2
        else:
            mediane = None
        
        # % réussite (correct)
        pct_reussite = round((nb_correct / nb_tot) * 100, 1) if nb_tot else 0
        # % conformité (rag_used)
        pct_conformite = round((nb_rag / nb_tot) * 100, 1) if nb_tot else 0

        # Durée session
        if started_at and ended_at:
            try:
                dt1 = datetime.datetime.fromisoformat(started_at)
                dt2 = datetime.datetime.fromisoformat(ended_at)
                duree = str(dt2 - dt1)
            except:
                duree = "-"
        else:
            duree = "-"

        sessions.append({
            "id": sess_id,
            "username": username,
            "started_at": started_at,
            "ended_at": ended_at,
            "nb_tot": nb_tot or 0,
            "moyenne": round(moyenne, 2) if moyenne is not None else "-",
            "mediane": mediane if mediane is not None else "-",
            "mini": mini if mini is not None else "-",
            "maxi": maxi if maxi is not None else "-",
            "pct_reussite": pct_reussite,
            "pct_conformite": pct_conformite,
            "duree": duree,
        })

    conn.close()
    return render_template("admin_sessions.html", sessions=sessions)

@app.route("/home_admin")
@login_required
@admin_required
def home_admin():
    user = get_user_by_id(session["user_id"])

    # Récupère stats globales
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*),
               AVG(note),
               SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END),
               SUM(CASE WHEN rag_used=1 THEN 1 ELSE 0 END)
        FROM questions
    """)
    total_questions, avg_note, total_correct, total_conforme = cur.fetchone()

    # Évite division par zéro...
    pct_reussite = round((total_correct / total_questions) * 100, 1) if total_questions else 0
    pct_conformite = round((total_conforme / total_questions) * 100, 1) if total_questions else 0
    note_moy = round(avg_note, 2) if avg_note is not None else "-"

    conn.close()

    stats = {
        "total_questions": total_questions,
        "pct_reussite": pct_reussite,
        "pct_conformite": pct_conformite,
        "note_moy": note_moy
    }
    return render_template("home_admin.html", user=user, stats=stats)

@app.route("/admin/sessions/<int:session_id>/export")
@login_required
@admin_required
def admin_export_session(session_id):
    conn = get_db()
    cur = conn.cursor()
    # Infos session
    cur.execute("""
        SELECT s.id, u.username, s.started_at, s.ended_at
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.id=%s
    """, (session_id,))
    row = cur.fetchone()
    if not row:
        return "Session non trouvée", 404
    session_data = {
        "id": row[0],
        "username": row[1],
        "started_at": row[2],
        "ended_at": row[3]
    }
    # Questions/réponses
    cur.execute("""
        SELECT question_text, answer_text, appreciation, note, is_correct, rag_used, created_at
        FROM questions WHERE session_id=%s
        ORDER BY created_at ASC
    """, (session_id,))
    questions = cur.fetchall()
    conn.close()
    # Rendu "ultra user friendly" (on peut pousser le style ensuite)
    return render_template("admin_export_session.html", session=session_data, questions=questions)
import openai

@app.route("/api/generate_question", methods=["POST"])
@login_required
def api_generate_question():
    if session.get("is_admin"):
        return {"ok": False, "error": "Accès non autorisé."}, 403

    try:
        openai.api_key = "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"
        assistant_id = "asst_6dTYk2SopQ3caUrrO2uhsaas"  # Remplace par l’ID de ton Assistant

        # Créer un thread temporaire
        thread = openai.beta.threads.create()
        # Ajoute un message au thread pour déclencher la génération
        openai.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content="Génère une question originale pour un test de pilote, difficulté intermédiaire."
        )
        # Démarre la run
        run = openai.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant_id,
        )

        # Attend que la run soit terminée
        import time
        status = None
        for _ in range(20):
            run_status = openai.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            status = run_status.status
            if status in ("completed", "failed", "cancelled", "expired"):
                break
            time.sleep(1)
        if status != "completed":
            return {"ok": False, "error": f"Run status: {status}"}, 500

        # Récupérer le message généré par l’assistant
        messages = openai.beta.threads.messages.list(thread_id=thread.id)
        for m in reversed(messages.data):
            if m.role == "assistant":
                content = m.content[0].text.value.strip()
                return {"ok": True, "question": content}
        return {"ok": False, "error": "Pas de réponse générée."}, 500

    except Exception as e:
        return {"ok": False, "error": str(e)}, 500



if __name__ == "__main__":
    app.run(debug=True)
