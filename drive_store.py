"""Google Drive appDataFolder storage for the ESTN Streamlit prototype.

Append-only records avoid overwriting other users' claims during independent
updates. Google Drive is not a transactional database; this is intended for
small, controlled pilots, not a multi-user institutional production system.

OAuth scope: https://www.googleapis.com/auth/drive.appdata
A *new*, private authorized_user token is required; never reuse exposed tokens.
"""
import base64
import csv
import hashlib
import io
import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from werkzeug.security import check_password_hash, generate_password_hash
from student_management import parse_csv_preview, normalize_profile, public_student
from security_controls import identity_key, is_limited, audit_event, WINDOW_SECONDS

SCOPE = "https://www.googleapis.com/auth/drive.appdata"
PREFIX = "estn_v2_"
CATEGORIES = ("Pédagogique", "Technique", "Administrative", "Autre")
STATUSES = ("Soumise", "En cours", "Acceptée", "Rejetée", "Clôturée")
CSV_COLUMNS = ("num_apogee", "nom_etudiant", "prenom", "email", "filiere", "niveau")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _name(kind):
    return f"{PREFIX}{kind}_{time.time_ns()}_{uuid.uuid4().hex}"


def _validate_file(filename, payload):
    suffix = Path(filename or "").suffix.lower()
    signatures = {".pdf": (b"%PDF-", "application/pdf"),
                  ".png": (b"\x89PNG\r\n\x1a\n", "image/png"),
                  ".jpg": (b"\xff\xd8\xff", "image/jpeg"),
                  ".jpeg": (b"\xff\xd8\xff", "image/jpeg")}
    if suffix not in signatures or not payload or len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("Pièce jointe invalide (PDF, PNG ou JPEG, 5 Mo maximum).")
    signature, mime = signatures[suffix]
    if not payload.startswith(signature):
        raise ValueError("Signature du fichier incorrecte.")
    return mime


def _public_user(u):
    result = {key: u[key] for key in (
        "id", "email", "role", "first_name", "last_name",
        "program", "level", "password_change_required",
    )}
    result["is_active"] = u.get("is_active", True)
    result["permission_version"] = u.get("permission_version", 0)
    return result


class DriveStore:
    """Minimal Drive API adapter; injectable _list/_read/_write for unit tests."""

    def __init__(self, encoded_token):
        try:
            raw = base64.b64decode(encoded_token, validate=True)
            data = json.loads(raw)
            scopes = data.get("scopes") or []
            if SCOPE not in scopes:
                raise ValueError("Une nouvelle autorisation Google Drive avec le scope drive.appdata est nécessaire.")
            required = ("refresh_token", "client_id", "client_secret", "token_uri")
            if any(not data.get(field) for field in required):
                raise ValueError("Le nouveau jeton ne contient pas toutes les informations OAuth nécessaires.")
            creds = Credentials.from_authorized_user_info(data, scopes=[SCOPE])
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("GDRIVE_TOKEN_B64 invalide ou autorisation Google insuffisante. Générer un nouveau jeton.") from None
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._lock = threading.RLock()
        # Fail early with a safe diagnostic handled by the Streamlit front end.
        self._list()

    def _list(self):
        """List application-owned files, handling Drive pagination."""
        result, page_token = [], None
        while True:
            response = self.service.files().list(
                spaces="appDataFolder",
                q="name contains 'estn_v2_' and trashed = false",
                fields="nextPageToken,files(id,name)",
                pageSize=1000, pageToken=page_token,
            ).execute()
            result.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return [f for f in result if f.get("name", "").startswith(PREFIX)]

    def _read(self, file_id):
        content = self.service.files().get_media(fileId=file_id).execute()
        return json.loads(content.decode("utf-8"))

    def _write(self, kind, value, content_type="application/json"):
        payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                   if content_type == "application/json" else value)
        name = _name(kind)
        self.service.files().create(
            body={"name": name, "parents": ["appDataFolder"],
                  "mimeType": content_type},
            media_body=MediaIoBaseUpload(
                io.BytesIO(payload), mimetype=content_type, resumable=False
            ), fields="id",
        ).execute()
        return name

    def _binary(self, file_id):
        return self.service.files().get_media(fileId=file_id).execute()

    def _files(self, prefix, files=None):
        return [f for f in (files if files is not None else self._list())
                if f["name"].startswith(PREFIX + prefix)]

    def _users(self, files=None):
        """Read batches and immutable password-change events."""
        files = self._list() if files is None else files
        users = {}
        for file in self._files("users_", files):
            for u in self._read(file["id"])["users"]:
                users[u["id"]] = u
        revisions = sorted(self._files("password_", files), key=lambda f: f["name"])
        for file in revisions:
            record = self._read(file["id"])
            if record["user_id"] in users:
                users[record["user_id"]]["password_hash"] = record["password_hash"]
                users[record["user_id"]]["password_change_required"] = False
        for file in sorted(self._files("student_update_", files), key=lambda f: f["name"]):
            revision = self._read(file["id"])
            if revision.get("user_id") in users and users[revision["user_id"]]["role"] == "student":
                users[revision["user_id"]].update(revision["updates"])
        for user in users.values():
            user.setdefault("is_active", True)
        return users

    def _user(self, user_id, role=None, users=None):
        users = self._users() if users is None else users
        u = users.get(user_id)
        if u is None or not u.get("is_active", True) or (role and u["role"] != role):
            raise PermissionError("Accès interdit.")
        return u

    def get_user(self, user_id):
        u = self._users().get(user_id)
        return _public_user(u) if u and u.get("is_active", True) else None

    def authenticate(self, email, password):
        email = (email or "").strip().lower()
        key = identity_key(email)
        now = time.time()
        with self._lock:
            recent = []
            for file in sorted(self._files("auth_"), key=lambda f: f["name"]):
                event = self._read(file["id"])
                if event.get("identity") == key and event.get("at", 0) >= now - WINDOW_SECONDS:
                    recent.append((event["at"], event["action"]))
            if is_limited(recent, now=now):
                self._write("auth", {"identity": key, "action": "limited", "at": now})
                audit_event("login_limited", email)
                return None
            for user in self._users().values():
                if (user["email"] == email and user.get("is_active", True)
                        and check_password_hash(user["password_hash"], password)):
                    self._write("auth", {"identity": key, "action": "success", "at": now})
                    audit_event("login_success", email)
                    return _public_user(user)
            self._write("auth", {"identity": key, "action": "failed", "at": now})
            audit_event("login_failed", email)
            return None

    def bootstrap_admin(self, email, password):
        email = (email or "").strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password or "") < 12:
            raise ValueError("Adresse e-mail valide et mot de passe admin de 12 caractères minimum requis.")
        with self._lock:
            users = self._users()
            if any(u["role"] == "admin" for u in users.values()):
                return False
            if any(u["email"] == email for u in users.values()):
                raise ValueError("L'adresse e-mail administrateur existe déjà.")
            user = {
                "id": uuid.uuid4().hex, "student_number": None,
                "first_name": "Administration", "last_name": "ESTN",
                "email": email, "program": "", "level": "",
                "role": "admin", "password_hash": generate_password_hash(password),
                "password_change_required": True,
            }
            self._write("users", {"users": [user]})
            return True

    def change_password(self, user_id, old_password, new_password):
        if len(new_password or "") < 12 or new_password == old_password:
            raise ValueError("Choisissez un nouveau mot de passe différent, de 12 caractères minimum.")
        with self._lock:
            user = self._user(user_id)
            if not check_password_hash(user["password_hash"], old_password):
                raise ValueError("Mot de passe actuel incorrect.")
            self._write("password", {
                "user_id": user_id, "password_hash": generate_password_hash(new_password),
            })

    def _claims(self, files=None):
        files = self._list() if files is None else files
        claims = {}
        for f in self._files("claim_", files):
            c = self._read(f["id"])
            claims[c["id"]] = c
        replies = []
        for f in self._files("reply_", files):
            replies.append((f["name"], self._read(f["id"])))
        for _, r in sorted(replies, key=lambda entry: entry[0]):
            if r["claim_id"] in claims:
                claims[r["claim_id"]]["status"] = r["status"]
                claims[r["claim_id"]]["admin_response"] = r["response"]
                claims[r["claim_id"]]["updated_at"] = r["created_at"]
        return claims

    def add_claim(self, user_id, category, subject, description, filename=None, payload=None):
        subject, description = (subject or "").strip(), (description or "").strip()
        if category not in CATEGORIES or not 4 <= len(subject) <= 150 or not 10 <= len(description) <= 5000:
            raise ValueError("Catégorie, objet (4–150) ou description (10–5000) invalide.")
        self._user(user_id, "student")
        mime = _validate_file(filename, payload) if filename else None
        attachment_id = None
        claim_id = uuid.uuid4().hex
        when = _now()
        with self._lock:
            if filename:
                # Store private file first. Unreferenced upload possible if claim write fails.
                attachment_id = self._write("attachment", payload, mime)
            self._write("claim", {
                "id": claim_id, "student_id": user_id, "category": category,
                "subject": subject, "description": description, "status": "Soumise",
                "admin_response": "", "created_at": when, "updated_at": when,
                "attachment_name": Path(filename).name[:200] if filename else None,
                "attachment_record": attachment_id,
            })
        return claim_id

    def list_claims(self, user_id, category=None, status=None):
        users = self._users()
        actor = self._user(user_id, users=users)
        result = []
        for c in self._claims().values():
            if actor["role"] != "admin" and c["student_id"] != user_id:
                continue
            if category in CATEGORIES and c["category"] != category:
                continue
            if status in STATUSES and c["status"] != status:
                continue
            item = dict(c)
            student = users.get(c["student_id"], {})
            item["student_name"] = (student.get("first_name", "") + " " + student.get("last_name", "")).strip()
            item["program"] = student.get("program", "")
            result.append(item)
        return sorted(result, key=lambda c: (c["created_at"], c["id"]), reverse=True)

    def _claim(self, user_id, claim_id):
        actor = self._user(user_id)
        claims = self._claims()
        claim = claims.get(claim_id)
        if not claim:
            raise LookupError("Réclamation introuvable.")
        if actor["role"] != "admin" and claim["student_id"] != user_id:
            audit_event("claim_access_denied", user_id, claim_id)
            raise PermissionError("Réclamation privée.")
        audit_event("claim_view", user_id, claim_id)
        return claim

    def claim_detail(self, user_id, claim_id):
        claim = self._claim(user_id, claim_id)
        result = dict(claim)
        owners = self._users()
        student = owners.get(claim["student_id"], {})
        result["student_name"] = (student.get("first_name", "") + " " + student.get("last_name", "")).strip()
        result["program"] = student.get("program", "")
        history = [{
            "action": "Réclamation soumise", "note": "",
            "at": claim["created_at"], "by": result["student_name"],
        }]
        for f in self._files("reply_"):
            r = self._read(f["id"])
            if r["claim_id"] == claim_id:
                admin = owners.get(r["admin_id"], {})
                history.append({
                    "action": "Statut : " + r["status"],
                    "note": r["response"],
                    "at": r["created_at"],
                    "by": (admin.get("first_name", "") + " " + admin.get("last_name", "")).strip(),
                })
        result["history"] = sorted(history, key=lambda event: event["at"], reverse=True)
        result["has_attachment"] = bool(claim.get("attachment_record"))
        result["original_filename"] = claim.get("attachment_name")
        return result

    def get_attachment(self, user_id, claim_id):
        claim = self._claim(user_id, claim_id)
        name = claim.get("attachment_record")
        if not name:
            return None
        matches = [f for f in self._files("attachment_") if f["name"] == name]
        if len(matches) != 1:
            raise LookupError("Pièce jointe inaccessible.")
        filename = claim["attachment_name"]
        suffix = Path(filename).suffix.lower()
        mime = {".pdf": "application/pdf", ".png": "image/png",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}[suffix]
        return filename, mime, self._binary(matches[0]["id"])

    def respond(self, admin_id, claim_id, status, response):
        response = (response or "").strip()
        if status not in STATUSES or len(response) > 5000:
            raise ValueError("Statut ou réponse invalide.")
        if status in ("Acceptée", "Rejetée", "Clôturée") and len(response) < 10:
            raise ValueError("La décision nécessite une réponse motivée de 10 caractères minimum.")
        with self._lock:
            self._user(admin_id, "admin")
            self._claim(admin_id, claim_id)
            self._write("reply", {
                "claim_id": claim_id, "admin_id": admin_id,
                "status": status, "response": response, "created_at": _now(),
            })

    def import_students(self, admin_id, csv_text):
        try:
            reader = csv.DictReader(io.StringIO(csv_text, newline=""))
            headers = reader.fieldnames or []
            if not set(CSV_COLUMNS).issubset(headers):
                raise ValueError("Colonnes CSV manquantes.")
            if "mot_de_passe" in headers or "password" in headers:
                raise ValueError("Les mots de passe en clair ne sont pas acceptés.")
            rows = list(reader)
            if not 1 <= len(rows) <= 1000:
                raise ValueError("Le fichier doit contenir entre 1 et 1000 étudiants.")
            cleaned, seen_email, seen_apogee = [], set(), set()
            for line, record in enumerate(rows, 2):
                item = {c: (record.get(c) or "").strip() for c in CSV_COLUMNS}
                item["email"] = item["email"].lower()
                if any(not v or len(v) > 200 for v in item.values()):
                    raise ValueError(f"Ligne {line} : champ vide ou trop long.")
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", item["email"]):
                    raise ValueError(f"Ligne {line} : adresse e-mail invalide.")
                if item["email"] in seen_email or item["num_apogee"] in seen_apogee:
                    raise ValueError(f"Ligne {line} : doublon dans le CSV.")
                seen_email.add(item["email"])
                seen_apogee.add(item["num_apogee"])
                cleaned.append(item)
        except csv.Error as exc:
            raise ValueError("CSV mal formé.") from exc

        with self._lock:
            self._user(admin_id, "admin")
            existing = self._users().values()
            used_email = {u["email"] for u in existing}
            used_apogee = {u["student_number"] for u in existing}
            if seen_email & used_email or seen_apogee & used_apogee:
                raise ValueError("Un étudiant est déjà enregistré. Import annulé.")
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["num_apogee", "email", "mot_de_passe_temporaire"])
            users = []
            for item in cleaned:
                password = secrets.token_urlsafe(20)
                users.append({
                    "id": uuid.uuid4().hex,
                    "student_number": item["num_apogee"],
                    "first_name": item["prenom"], "last_name": item["nom_etudiant"],
                    "email": item["email"], "program": item["filiere"],
                    "level": item["niveau"], "role": "student",
                    "password_hash": generate_password_hash(password),
                    "password_change_required": True,
                })
                writer.writerow([item["num_apogee"], item["email"], password])
            # A single immutable Drive file = all-or-none for one CSV batch.
            self._write("users", {"users": users})
            return output.getvalue()

    def preview_students(self, admin_id, csv_text):
        rows, issues = parse_csv_preview(csv_text)
        with self._lock:
            self._user(admin_id, "admin")
            users = list(self._users().values())
            emails = {u["email"] for u in users}
            numbers = {u.get("student_number") for u in users}
            for idx, row in enumerate(rows, 2):
                if row["email"] in emails or row["num_apogee"] in numbers:
                    issues.append(f"Ligne {idx} : compte déjà enregistré (e-mail ou APOGÉE).")
        return {"rows": rows, "issues": issues}

    def list_students(self, admin_id, search="", program="", level="", active="Tous"):
        users = self._users()
        self._user(admin_id, "admin", users)
        result = []
        for u in users.values():
            if u["role"] != "student":
                continue
            if search and search.strip().lower() not in " ".join(str(u.get(k, "")) for k in
                        ("student_number", "first_name", "last_name", "email")).lower():
                continue
            if program and program.strip().lower() not in u["program"].lower():
                continue
            if level and level.strip().lower() not in u["level"].lower():
                continue
            if active == "Actifs" and not u.get("is_active", True):
                continue
            if active == "Inactifs" and u.get("is_active", True):
                continue
            result.append(public_student(u))
        return sorted(result, key=lambda u: str(u["student_number"]))[:500]

    def update_student(self, admin_id, student_id, updates):
        clean = normalize_profile(updates)
        with self._lock:
            self._user(admin_id, "admin")
            users = self._users()
            target = users.get(student_id)
            if target is None or target["role"] != "student":
                raise LookupError("Étudiant introuvable.")
            for other in users.values():
                if other["id"] == student_id:
                    continue
                if "email" in clean and other["email"] == clean["email"]:
                    raise ValueError("E-mail déjà utilisé.")
                if "student_number" in clean and other.get("student_number") == clean["student_number"]:
                    raise ValueError("Numéro APOGÉE déjà utilisé.")
            self._write("student_update", {"user_id": student_id, "updates": clean})
            audit_event("student_profile_updated", admin_id, student_id)


def make_engine(encoded_token):
    return DriveStore(encoded_token)


def initialize(engine):
    """No database migration needed: appDataFolder is created by Google Drive."""
    return None


def bootstrap_admin(engine, email, password):
    return engine.bootstrap_admin(email, password)


def get_user(engine, user_id):
    return engine.get_user(user_id)


def authenticate(engine, email, password):
    return engine.authenticate(email, password)


def change_password(engine, user_id, old_password, new_password):
    return engine.change_password(user_id, old_password, new_password)


def add_claim(engine, student_id, category, subject, description, filename=None, payload=None):
    return engine.add_claim(student_id, category, subject, description, filename, payload)


def list_claims(engine, user_id, category=None, status=None):
    return engine.list_claims(user_id, category, status)


def claim_detail(engine, user_id, claim_id):
    return engine.claim_detail(user_id, claim_id)


def get_attachment(engine, user_id, claim_id):
    return engine.get_attachment(user_id, claim_id)


def respond(engine, admin_id, claim_id, status, response):
    return engine.respond(admin_id, claim_id, status, response)


def import_students(engine, admin_id, csv_text):
    return engine.import_students(admin_id, csv_text)


def preview_students(engine, admin_id, csv_text):
    return engine.preview_students(admin_id, csv_text)


def list_students(engine, admin_id, search="", program="", level="", active="Tous"):
    return engine.list_students(admin_id, search, program, level, active)


def update_student(engine, admin_id, student_id, updates):
    return engine.update_student(admin_id, student_id, updates)
