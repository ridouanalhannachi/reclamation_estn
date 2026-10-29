"""Backend partagé par l'interface Streamlit (PostgreSQL en production).

La base est explicitement fournie via DATABASE_URL / st.secrets, jamais stockée
dans le dépôt. Les pièces jointes de cette interface sont dans la base externe.
"""
import csv
import io
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint, create_engine, select, func,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import declarative_base, sessionmaker
from werkzeug.security import check_password_hash, generate_password_hash

Base = declarative_base()
STATUSES = ("Soumise", "En cours", "Acceptée", "Rejetée", "Clôturée")
CATEGORIES = ("Pédagogique", "Technique", "Administrative", "Autre")
CSV_COLUMNS = ("num_apogee", "nom_etudiant", "prenom", "email", "filiere", "niveau")
ALLOWED_EXTENSIONS = {".pdf": "application/pdf", ".png": "image/png",
                      ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    student_number = Column(String(200), unique=True)
    last_name = Column(String(200), nullable=False)
    first_name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False, unique=True)
    program = Column(String(200), nullable=False, default="")
    level = Column(String(200), nullable=False, default="")
    password_hash = Column(Text, nullable=False)
    password_change_required = Column(Integer, nullable=False, default=0)
    role = Column(String(20), nullable=False)


class Claim(Base):
    __tablename__ = "claims"
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String(100), nullable=False)
    subject = Column(String(150), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(50), nullable=False, default="Soumise")
    admin_response = Column(Text, nullable=False, default="")
    attachment_path = Column(String(250))
    original_filename = Column(String(200))
    created_at = Column(String(50), nullable=False)
    updated_at = Column(String(50), nullable=False)


class ClaimEvent(Base):
    __tablename__ = "history"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(200), nullable=False)
    note = Column(Text, nullable=False, default="")
    created_at = Column(String(50), nullable=False)


class ClaimAttachment(Base):
    __tablename__ = "claim_attachments"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"), nullable=False, unique=True)
    original_filename = Column(String(200), nullable=False)
    mime_type = Column(String(100), nullable=False)
    payload = Column(LargeBinary, nullable=False)


def make_engine(database_url):
    if not database_url:
        raise ValueError("DATABASE_URL est obligatoire.")
    if database_url.startswith("postgres://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
    elif database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
    if not (database_url.startswith("postgresql+psycopg://")
            or database_url.startswith("sqlite:///")):
        raise ValueError("Utilisez une URL PostgreSQL ou SQLite locale.")
    return create_engine(database_url, pool_pre_ping=True)


def initialize(engine):
    Base.metadata.create_all(engine)


def _session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)()


def public_user(user):
    return {
        "id": user.id, "email": user.email, "role": user.role,
        "first_name": user.first_name, "last_name": user.last_name,
        "program": user.program, "level": user.level,
        "password_change_required": bool(user.password_change_required),
    }


def get_user(engine, user_id):
    with _session(engine) as s:
        user = s.get(User, user_id)
        return public_user(user) if user is not None else None


def authenticate(engine, email, password):
    with _session(engine) as s:
        user = s.scalar(select(User).where(User.email == email.strip().lower()))
        if not user or not check_password_hash(user.password_hash, password):
            return None
        return public_user(user)


def bootstrap_admin(engine, email, password):
    """Créer un admin uniquement si aucun admin n'existe (première installation)."""
    email = (email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password or "") < 12:
        raise ValueError("E-mail admin valide et mot de passe initial >= 12 caractères requis.")
    with _session(engine) as s:
        with s.begin():
            if s.scalar(select(func.count(User.id)).where(User.role == "admin")):
                return False
            s.add(User(
                first_name="Administration", last_name="ESTN", email=email,
                program="", level="", role="admin",
                password_hash=generate_password_hash(password),
                password_change_required=1,
            ))
    return True


def change_password(engine, user_id, current_password, new_password):
    if len(new_password or "") < 12 or new_password == current_password:
        raise ValueError("Le nouveau mot de passe doit être différent et contenir 12 caractères minimum.")
    with _session(engine) as s:
        with s.begin():
            user = s.get(User, user_id)
            if not user or not check_password_hash(user.password_hash, current_password):
                raise ValueError("Mot de passe actuel incorrect.")
            user.password_hash = generate_password_hash(new_password)
            user.password_change_required = 0


def _require_user(s, user_id, role=None):
    user = s.get(User, user_id)
    if not user or (role and user.role != role):
        raise PermissionError("Accès interdit.")
    return user


def validate_attachment(filename, payload):
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Pièce jointe non autorisée : PDF, PNG ou JPEG.")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ValueError("Pièce jointe trop volumineuse (5 Mo maximum).")
    if suffix == ".pdf" and not payload.startswith(b"%PDF-"):
        raise ValueError("Signature du PDF incorrecte.")
    if suffix == ".png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Signature du PNG incorrecte.")
    if suffix in (".jpg", ".jpeg") and not payload.startswith(b"\xff\xd8\xff"):
        raise ValueError("Signature du JPEG incorrecte.")
    return ALLOWED_EXTENSIONS[suffix]


def add_claim(engine, student_id, category, subject, description, filename=None, payload=None):
    subject, description = (subject or "").strip(), (description or "").strip()
    if category not in CATEGORIES or not 4 <= len(subject) <= 150 or not 10 <= len(description) <= 5000:
        raise ValueError("Catégorie, objet (4-150) ou description (10-5000) invalide.")
    mime = None
    if filename:
        if payload is None:
            raise ValueError("Pièce jointe vide.")
        mime = validate_attachment(filename, payload)
    time = now_utc()
    with _session(engine) as s:
        with s.begin():
            _require_user(s, student_id, "student")
            claim = Claim(
                student_id=student_id, category=category, subject=subject,
                description=description, status="Soumise", admin_response="",
                created_at=time, updated_at=time,
                original_filename=Path(filename).name[:200] if filename else None,
            )
            s.add(claim)
            s.flush()
            s.add(ClaimEvent(claim_id=claim.id, actor_id=student_id,
                             action="Réclamation soumise", note="", created_at=time))
            if filename:
                s.add(ClaimAttachment(
                    claim_id=claim.id, original_filename=Path(filename).name[:200],
                    mime_type=mime, payload=payload,
                ))
            return claim.id


def claim_dict(claim, student=None):
    return {
        "id": claim.id, "student_id": claim.student_id,
        "category": claim.category, "subject": claim.subject,
        "description": claim.description, "status": claim.status,
        "admin_response": claim.admin_response or "",
        "original_filename": claim.original_filename,
        "created_at": claim.created_at, "updated_at": claim.updated_at,
        "student_name": (student.first_name + " " + student.last_name) if student else "",
        "program": student.program if student else "",
    }


def list_claims(engine, user_id, category=None, status=None):
    with _session(engine) as s:
        user = _require_user(s, user_id)
        stmt = select(Claim, User).join(User, Claim.student_id == User.id)
        if user.role == "student":
            stmt = stmt.where(Claim.student_id == user_id)
        elif user.role != "admin":
            raise PermissionError("Rôle non autorisé.")
        if category in CATEGORIES:
            stmt = stmt.where(Claim.category == category)
        if status in STATUSES:
            stmt = stmt.where(Claim.status == status)
        stmt = stmt.order_by(Claim.id.desc())
        return [claim_dict(claim, student) for claim, student in s.execute(stmt).all()]


def claim_detail(engine, user_id, claim_id):
    with _session(engine) as s:
        user = _require_user(s, user_id)
        record = s.execute(
            select(Claim, User).join(User, Claim.student_id == User.id)
            .where(Claim.id == claim_id)
        ).first()
        if record is None:
            raise LookupError("Réclamation introuvable.")
        claim, student = record
        if user.role != "admin" and claim.student_id != user_id:
            raise PermissionError("Cette réclamation appartient à un autre étudiant.")
        events = s.execute(
            select(ClaimEvent, User).join(User, ClaimEvent.actor_id == User.id)
            .where(ClaimEvent.claim_id == claim_id).order_by(ClaimEvent.id.desc())
        ).all()
        details = claim_dict(claim, student)
        details["history"] = [
            {"action": event.action, "note": event.note,
             "at": event.created_at,
             "by": actor.first_name + " " + actor.last_name}
            for event, actor in events
        ]
        details["has_attachment"] = s.scalar(
            select(func.count(ClaimAttachment.id)).where(ClaimAttachment.claim_id == claim_id)
        ) > 0
        return details


def get_attachment(engine, user_id, claim_id):
    with _session(engine) as s:
        user = _require_user(s, user_id)
        claim = s.get(Claim, claim_id)
        if not claim:
            raise LookupError("Réclamation introuvable.")
        if user.role != "admin" and claim.student_id != user_id:
            raise PermissionError("Pièce jointe privée.")
        attachment = s.scalar(
            select(ClaimAttachment).where(ClaimAttachment.claim_id == claim_id)
        )
        if not attachment:
            return None
        return (attachment.original_filename, attachment.mime_type, attachment.payload)


def respond(engine, admin_id, claim_id, status, response):
    response = (response or "").strip()
    if status not in STATUSES or len(response) > 5000:
        raise ValueError("Statut ou réponse invalide.")
    if status in ("Acceptée", "Rejetée", "Clôturée") and len(response) < 10:
        raise ValueError("Une décision exige une réponse motivée d'au moins 10 caractères.")
    with _session(engine) as s:
        with s.begin():
            _require_user(s, admin_id, "admin")
            claim = s.get(Claim, claim_id)
            if claim is None:
                raise LookupError("Réclamation introuvable.")
            when = now_utc()
            claim.status = status
            claim.admin_response = response
            claim.updated_at = when
            s.add(ClaimEvent(
                claim_id=claim_id, actor_id=admin_id,
                action="Statut : " + status, note=response, created_at=when,
            ))


def import_students(engine, admin_id, csv_text):
    try:
        reader = csv.DictReader(io.StringIO(csv_text, newline=""))
        headers = reader.fieldnames or []
        if not set(CSV_COLUMNS).issubset(headers):
            raise ValueError("Colonnes CSV obligatoires manquantes.")
        if "mot_de_passe" in headers or "password" in headers:
            raise ValueError("Ne jamais importer de mots de passe en clair.")
        rows = list(reader)
        if not 1 <= len(rows) <= 1000:
            raise ValueError("Le fichier doit contenir entre 1 et 1000 étudiants.")
        cleaned = []
        seen_email, seen_apogee = set(), set()
        for line, row in enumerate(rows, 2):
            value = {col: (row.get(col) or "").strip() for col in CSV_COLUMNS}
            value["email"] = value["email"].lower()
            if any(not item or len(item) > 200 for item in value.values()):
                raise ValueError(f"Ligne {line} : champ obligatoire vide ou trop long.")
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value["email"]):
                raise ValueError(f"Ligne {line} : e-mail incorrect.")
            if value["email"] in seen_email or value["num_apogee"] in seen_apogee:
                raise ValueError(f"Ligne {line} : doublon dans le fichier.")
            seen_email.add(value["email"])
            seen_apogee.add(value["num_apogee"])
            cleaned.append(value)
    except csv.Error as exc:
        raise ValueError("Fichier CSV mal formé.") from exc

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["num_apogee", "email", "mot_de_passe_temporaire"])
    try:
        with _session(engine) as s:
            with s.begin():
                _require_user(s, admin_id, "admin")
                for item in cleaned:
                    if s.scalar(select(User.id).where(
                        (User.email == item["email"])
                        | (User.student_number == item["num_apogee"])
                    )):
                        raise ValueError("Compte déjà présent (e-mail ou numéro APOGÉE).")
                    password = secrets.token_urlsafe(20)
                    s.add(User(
                        student_number=item["num_apogee"],
                        last_name=item["nom_etudiant"], first_name=item["prenom"],
                        email=item["email"], program=item["filiere"],
                        level=item["niveau"], role="student",
                        password_hash=generate_password_hash(password),
                        password_change_required=1,
                    ))
                    writer.writerow([item["num_apogee"], item["email"], password])
    except IntegrityError as exc:
        raise ValueError("Un numéro APOGÉE ou e-mail existe déjà.") from exc
    return output.getvalue()
