"""Réclamations ESTN — application Flask légère, sans comptes prédéfinis."""
import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import click
from flask import (
    Flask, abort, current_app, flash, g, redirect, render_template,
    request, Response, send_from_directory, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

STATUSES = ("Soumise", "En cours", "Acceptée", "Rejetée", "Clôturée")
CATEGORIES = ("Pédagogique", "Technique", "Administrative", "Autre")
REQUIRED_CSV = ("num_apogee", "nom_etudiant", "prenom", "email", "filiere", "niveau")
ALLOWED_FILES = {".pdf", ".png", ".jpg", ".jpeg"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_number TEXT UNIQUE,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    program TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student', 'admin'))
);
CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES users(id),
    category TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Soumise',
    admin_response TEXT NOT NULL DEFAULT '',
    attachment_path TEXT,
    original_filename TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id),
    actor_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_claim_student ON claims(student_id);
CREATE INDEX IF NOT EXISTS idx_history_claim ON history(claim_id);
"""


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def db():
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        g.db = connection
    return g.db


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def login_required(role=None):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            if role is not None and g.user["role"] != role:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def allowed_attachment(file):
    if not file or not file.filename:
        return True
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_FILES:
        return False
    prefix = file.stream.read(8)
    file.stream.seek(0)
    if suffix == ".pdf":
        return prefix.startswith(b"%PDF-")
    if suffix == ".png":
        return prefix.startswith(b"\x89PNG\r\n\x1a\n")
    return prefix.startswith(b"\xff\xd8\xff")


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get("ESTN_SECRET_KEY"),
        DATABASE=os.environ.get("ESTN_DATABASE", str(Path(app.instance_path) / "estn.sqlite3")),
        UPLOAD_FOLDER=os.environ.get("ESTN_UPLOAD_FOLDER", str(Path(app.instance_path) / "uploads")),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("ESTN_HTTPS", "0") == "1",
        PREFERRED_URL_SCHEME="https",
    )
    if test_config:
        app.config.update(test_config)
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("Définir ESTN_SECRET_KEY (32 octets aléatoires minimum).")
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    @app.teardown_appcontext
    def close_db(error=None):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.before_request
    def auth_and_csrf():
        g.user = None
        if session.get("user_id"):
            try:
                g.user = db().execute(
                    "SELECT * FROM users WHERE id = ?", (session["user_id"],)
                ).fetchone()
            except sqlite3.OperationalError:
                g.user = None
            if g.user is None:
                session.clear()
        if request.method == "POST":
            expected = session.get("_csrf", "")
            submitted = request.form.get("_csrf", "")
            if not expected or not hmac.compare_digest(expected, submitted):
                abort(400, description="Jeton de formulaire invalide.")

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.context_processor
    def inject_shared():
        return {"csrf_token": csrf_token, "statuses": STATUSES}

    @app.cli.command("init-db")
    def init_db_command():
        db().executescript(SCHEMA)
        db().commit()
        click.echo("Base de données initialisée.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--nom", prompt="Nom")
    @click.option("--prenom", prompt="Prénom")
    @click.password_option(confirmation_prompt=True)
    def create_admin(email, nom, prenom, password):
        if len(password) < 12:
            raise click.ClickException("Mot de passe administrateur : 12 caractères minimum.")
        with db():
            db().execute(
                "INSERT INTO users (last_name, first_name, email, password_hash, role) "
                "VALUES (?, ?, ?, ?, 'admin')",
                (nom.strip(), prenom.strip(), email.strip().lower(), generate_password_hash(password)),
            )
        click.echo("Compte administrateur créé.")

    @app.get("/")
    def index():
        if not g.user:
            return redirect(url_for("login"))
        return redirect(url_for("admin") if g.user["role"] == "admin" else url_for("student"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session.permanent = False
                csrf_token()
                return redirect(url_for("index"))
            flash("Identifiants invalides.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required()
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/student")
    @login_required("student")
    def student():
        claims = db().execute(
            "SELECT * FROM claims WHERE student_id=? ORDER BY created_at DESC, id DESC",
            (g.user["id"],),
        ).fetchall()
        return render_template("student.html", claims=claims)

    @app.route("/student/new", methods=["GET", "POST"])
    @login_required("student")
    def new_claim():
        if request.method == "POST":
            category = request.form.get("category", "")
            subject = request.form.get("subject", "").strip()
            description = request.form.get("description", "").strip()
            attachment = request.files.get("attachment")
            if category not in CATEGORIES or not (4 <= len(subject) <= 150) or not (10 <= len(description) <= 5000):
                flash("Vérifiez la catégorie, l'objet (4–150) et la description (10–5000).", "error")
                return render_template("new_claim.html", categories=CATEGORIES), 400
            if not allowed_attachment(attachment):
                flash("Fichier non valide : PDF, PNG ou JPEG uniquement.", "error")
                return render_template("new_claim.html", categories=CATEGORIES), 400
            stored_name = original_name = None
            if attachment and attachment.filename:
                original_name = Path(attachment.filename).name[:200]
                stored_name = uuid.uuid4().hex + Path(original_name).suffix.lower()
                attachment.save(Path(app.config["UPLOAD_FOLDER"]) / stored_name)
            created = utc_now()
            with db():
                cursor = db().execute(
                    "INSERT INTO claims (student_id, category, subject, description, status, "
                    "attachment_path, original_filename, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'Soumise', ?, ?, ?, ?)",
                    (g.user["id"], category, subject, description, stored_name, original_name, created, created),
                )
                db().execute(
                    "INSERT INTO history (claim_id, actor_id, action, created_at) VALUES (?, ?, ?, ?)",
                    (cursor.lastrowid, g.user["id"], "Réclamation soumise", created),
                )
            flash("Réclamation enregistrée.", "success")
            return redirect(url_for("claim_detail", claim_id=cursor.lastrowid))
        return render_template("new_claim.html", categories=CATEGORIES)

    @app.get("/claims/<int:claim_id>")
    @login_required()
    def claim_detail(claim_id):
        claim = db().execute(
            "SELECT c.*, u.first_name, u.last_name, u.program FROM claims c "
            "JOIN users u ON u.id=c.student_id WHERE c.id=?", (claim_id,)
        ).fetchone()
        if claim is None:
            abort(404)
        if g.user["role"] != "admin" and claim["student_id"] != g.user["id"]:
            abort(403)
        history = db().execute(
            "SELECT h.*, u.first_name, u.last_name FROM history h "
            "JOIN users u ON u.id=h.actor_id WHERE h.claim_id=? ORDER BY h.id DESC",
            (claim_id,),
        ).fetchall()
        return render_template("detail.html", claim=claim, history=history)

    @app.get("/attachments/<int:claim_id>")
    @login_required()
    def attachment(claim_id):
        claim = db().execute(
            "SELECT student_id, attachment_path, original_filename FROM claims WHERE id=?",
            (claim_id,),
        ).fetchone()
        if claim is None or not claim["attachment_path"]:
            abort(404)
        if g.user["role"] != "admin" and claim["student_id"] != g.user["id"]:
            abort(403)
        response = send_from_directory(
            app.config["UPLOAD_FOLDER"], claim["attachment_path"],
            as_attachment=True, download_name=claim["original_filename"],
        )
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/admin")
    @login_required("admin")
    def admin():
        category = request.args.get("category", "")
        status = request.args.get("status", "")
        where = []
        args = []
        if category in CATEGORIES:
            where.append("c.category=?")
            args.append(category)
        if status in STATUSES:
            where.append("c.status=?")
            args.append(status)
        sql = (
            "SELECT c.*, u.first_name, u.last_name, u.program FROM claims c "
            "JOIN users u ON u.id=c.student_id"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        claims = db().execute(sql + " ORDER BY c.created_at DESC, c.id DESC", args).fetchall()
        all_statuses = db().execute("SELECT status FROM claims").fetchall()
        return render_template(
            "admin.html", claims=claims, all_statuses=all_statuses,
            categories=CATEGORIES, selected_category=category, selected_status=status,
        )

    @app.post("/admin/claims/<int:claim_id>/respond")
    @login_required("admin")
    def respond(claim_id):
        claim = db().execute("SELECT id FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim:
            abort(404)
        status = request.form.get("status", "")
        note = request.form.get("response", "").strip()
        if status not in STATUSES or len(note) > 5000 or (status in ("Acceptée", "Rejetée", "Clôturée") and len(note) < 10):
            flash("Statut invalide ou réponse insuffisante (10 caractères pour une décision).", "error")
            return redirect(url_for("claim_detail", claim_id=claim_id))
        when = utc_now()
        with db():
            db().execute(
                "UPDATE claims SET status=?, admin_response=?, updated_at=? WHERE id=?",
                (status, note, when, claim_id),
            )
            db().execute(
                "INSERT INTO history (claim_id, actor_id, action, note, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (claim_id, g.user["id"], "Statut : " + status, note, when),
            )
        flash("Réclamation mise à jour.", "success")
        return redirect(url_for("claim_detail", claim_id=claim_id))

    @app.route("/admin/students/import", methods=["GET", "POST"])
    @login_required("admin")
    def import_students():
        if request.method == "GET":
            return render_template("import.html")
        upload = request.files.get("csv_file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            flash("Sélectionnez un fichier CSV.", "error")
            return redirect(url_for("import_students"))
        try:
            content = upload.stream.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(content))
            fieldnames = reader.fieldnames or []
            if not set(REQUIRED_CSV).issubset(set(fieldnames)):
                raise ValueError("Colonnes obligatoires manquantes.")
            if "mot_de_passe" in fieldnames:
                raise ValueError("Retirer la colonne mot_de_passe : aucun mot de passe en clair n'est importé.")
            rows = list(reader)
            if not 1 <= len(rows) <= 1000:
                raise ValueError("Le fichier doit contenir entre 1 et 1000 étudiants.")
            cleaned = []
            seen_email, seen_number = set(), set()
            for index, row in enumerate(rows, 2):
                values = {key: (row.get(key) or "").strip() for key in REQUIRED_CSV}
                values["email"] = values["email"].lower()
                if (
                    any(not value or len(value) > 200 for value in values.values())
                    or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", values["email"])
                    or values["email"] in seen_email
                    or values["num_apogee"] in seen_number
                    or db().execute(
                        "SELECT 1 FROM users WHERE email=? OR student_number=?",
                        (values["email"], values["num_apogee"]),
                    ).fetchone()
                ):
                    raise ValueError(f"Ligne {index} : données invalides ou doublon.")
                seen_email.add(values["email"])
                seen_number.add(values["num_apogee"])
                cleaned.append(values)
        except (UnicodeError, csv.Error, ValueError) as exc:
            flash(f"Import annulé : {exc}", "error")
            return redirect(url_for("import_students"))
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["num_apogee", "email", "mot_de_passe_temporaire"])
        with db():
            for values in cleaned:
                temporary_password = secrets.token_urlsafe(18)
                db().execute(
                    "INSERT INTO users (student_number, last_name, first_name, email, "
                    "program, level, password_hash, role) VALUES (?, ?, ?, ?, ?, ?, ?, 'student')",
                    (
                        values["num_apogee"], values["nom_etudiant"], values["prenom"],
                        values["email"], values["filiere"], values["niveau"],
                        generate_password_hash(temporary_password),
                    ),
                )
                writer.writerow([values["num_apogee"], values["email"], temporary_password])
        response = Response(output.getvalue(), mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = 'attachment; filename="identifiants-etudiants.csv"'
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(413)
    def too_large(error):
        return "Fichier trop volumineux (5 Mo maximum).", 413

    return app


if __name__ == "__main__":
    create_app().run(debug=False)
