"""SQL backend for services, assignments, complements and deadline reporting."""
from datetime import datetime, timezone
import sqlalchemy
from sqlalchemy import inspect, text
from administrative_workflow import validate_transition, append_status_history, utc_now
from administrative_deadlines import normalize_due_date

DEFAULT_SERVICES = (
    "Service de scolarité", "Service pédagogique", "Service informatique",
    "Service administratif général",
)


def _require_admin(connection, user_id):
    row = connection.execute(text("SELECT role, is_active FROM users WHERE id=:id"), {"id": user_id}).mappings().first()
    if row is None or not row["is_active"] or row["role"] not in ("admin", "super_admin"):
        raise PermissionError("Seul un administrateur habilité peut effectuer cette opération.")
    return row


def _require_actor(connection, user_id, student_id=None, claim_owner=None):
    row = connection.execute(text("SELECT role, is_active FROM users WHERE id=:id"), {"id": user_id}).mappings().first()
    if row is None or not row["is_active"]:
        raise PermissionError("Utilisateur inactif ou introuvable.")
    if student_id is not None and (row["role"] != "student" or user_id != claim_owner):
        raise PermissionError("Accès réservé à l'étudiant concerné.")
    return row


def initialize_administrative_schema(engine):
    """Additive, repeatable migration for SQL backends."""
    dialect = engine.dialect.name
    statements = [
        "CREATE TABLE IF NOT EXISTS administrative_services (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL UNIQUE, is_active INTEGER NOT NULL DEFAULT 1)",
        "CREATE TABLE IF NOT EXISTS administrative_assignments (id INTEGER PRIMARY KEY, claim_id INTEGER NOT NULL, service_id INTEGER, agent_id INTEGER, priority VARCHAR(20) NOT NULL DEFAULT 'normale', due_at VARCHAR(50), reason TEXT NOT NULL DEFAULT '', actor_id INTEGER NOT NOT NULL, created_at VARCHAR(50) NOT NULL)",
    ]
    # Correct portable wording for the possible typo avoided by separate creation where supported.
    statements[1] = statements[1].replace("actor_id INTEGER NOT NOT NULL", "actor_id INTEGER NOT NULL")
    if dialect == "postgresql":
        statements += [
            "CREATE TABLE IF NOT EXISTS administrative_status_history (id SERIAL PRIMARY KEY, claim_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, old_status VARCHAR(50) NOT NULL, new_status VARCHAR(50) NOT NULL, justification TEXT NOT NULL DEFAULT '', created_at VARCHAR(50) NOT NULL)",
            "CREATE TABLE IF NOT EXISTS administrative_complements (id SERIAL PRIMARY KEY, claim_id INTEGER NOT NULL, requester_id INTEGER NOT NULL, student_id INTEGER NOT NULL, kind VARCHAR(20) NOT NULL, message TEXT NOT NULL, original_filename VARCHAR(200), mime_type VARCHAR(100), payload BYTEA, created_at VARCHAR(50) NOT NULL)",
        ]
    else:
        statements += [
            "CREATE TABLE IF NOT EXISTS administrative_status_history (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id INTEGER NOT NULL, actor_id INTEGER NOT NULL, old_status VARCHAR(50) NOT NULL, new_status VARCHAR(50) NOT NULL, justification TEXT NOT NULL DEFAULT '', created_at VARCHAR(50) NOT NULL)",
            "CREATE TABLE IF NOT EXISTS administrative_complements (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id INTEGER NOT NULL, requester_id INTEGER NOT NULL, student_id INTEGER NOT NULL, kind VARCHAR(20) NOT NULL, message TEXT NOT NULL, original_filename VARCHAR(200), mime_type VARCHAR(100), payload BLOB, created_at VARCHAR(50) NOT NULL)",
        ]
    claim_columns = {
        "service_id": "INTEGER", "assigned_agent_id": "INTEGER",
        "assigned_at": "VARCHAR(50)", "priority": "VARCHAR(20)",
        "due_at": "VARCHAR(50)", "resolved_at": "VARCHAR(50)",
    }
    existing = {c["name"] for c in inspect(engine).get_columns("claims")}
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        for name, typ in claim_columns.items():
            if name not in existing:
                default = " DEFAULT 'normale'" if name == "priority" else ""
                conn.execute(text(f"ALTER TABLE claims ADD COLUMN {name} {typ}{default}"))
        for service in DEFAULT_SERVICES:
            conn.execute(text("INSERT INTO administrative_services(name, is_active) SELECT :name, 1 WHERE NOT EXISTS (SELECT 1 FROM administrative_services WHERE name=:name)"), {"name": service})


def services(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, name FROM administrative_services WHERE is_active=1 ORDER BY name")).mappings().all()
    return [dict(r) for r in rows]


def add_service(engine, admin_id, name):
    name = (name or "").strip()
    if not 2 <= len(name) <= 200:
        raise ValueError("Nom du service invalide (2 à 200 caractères).")
    with engine.begin() as conn:
        _require_admin(conn, admin_id)
        conn.execute(text("INSERT INTO administrative_services(name) VALUES (:name)"), {"name": name})


def assign_claim(engine, admin_id, claim_id, service_id, agent_id, priority, due_at, reason):
    reason = (reason or "").strip()
    if priority not in ("basse", "normale", "haute", "urgente"):
        raise ValueError("Priorité invalide.")
    if len(reason) < 3:
        raise ValueError("Un motif d'affectation est obligatoire.")
    due = normalize_due_date(due_at)
    with engine.begin() as conn:
        _require_admin(conn, admin_id)
        claim = conn.execute(text("SELECT status FROM claims WHERE id=:id"), {"id": claim_id}).mappings().first()
        if not claim:
            raise LookupError("Réclamation introuvable.")
        valid_agent = conn.execute(
            text("SELECT 1 FROM users WHERE id=:id AND role IN ('admin', 'agent') AND is_active=1"),
            {"id": agent_id},
        ).first()
        if not valid_agent:
            raise ValueError("Agent responsable invalide ou inactif.")
        if not conn.execute(text("SELECT 1 FROM administrative_services WHERE id=:id AND is_active=1"), {"id": service_id}).first():
            raise ValueError("Service invalide ou inactif.")
        old_status = claim["status"]
        conn.execute(text("UPDATE claims SET service_id=:service, assigned_agent_id=:agent, assigned_at=:at, priority=:priority, due_at=:due, status=:status, updated_at=:at WHERE id=:id"),
                     {"service": service_id, "agent": agent_id, "at": utc_now(), "priority": priority, "due": due, "status": "Affectée" if old_status == "Soumise" else old_status, "id": claim_id})
        conn.execute(text("INSERT INTO administrative_assignments(claim_id, service_id, agent_id, priority, due_at, reason, actor_id, created_at) VALUES (:claim,:service,:agent,:priority,:due,:reason,:actor,:at)"),
                     {"claim": claim_id, "service": service_id, "agent": agent_id, "priority": priority, "due": due, "reason": reason, "actor": admin_id, "at": utc_now()})
        if old_status == "Soumise":
            append_status_history(conn, claim_id, admin_id, old_status, "Affectée", reason)


def transition_status(engine, actor_id, claim_id, new_status, justification=""):
    with engine.begin() as conn:
        actor = _require_actor(conn, actor_id)
        claim = conn.execute(text("SELECT student_id, status, assigned_agent_id FROM claims WHERE id=:id"), {"id": claim_id}).mappings().first()
        if not claim:
            raise LookupError("Réclamation introuvable.")
        is_admin = actor["role"] == "admin"
        is_assignee = actor_id == claim["assigned_agent_id"]
        validate_transition(claim["status"], new_status, justification, is_assignee, is_admin)
        now = utc_now()
        resolution = now if new_status in ("Résolue", "Acceptée", "Rejetée", "Clôturée") else None
        conn.execute(text("UPDATE claims SET status=:status, updated_at=:at, resolved_at=COALESCE(:resolved, resolved_at) WHERE id=:id"), {"status": new_status, "at": now, "resolved": resolution, "id": claim_id})
        append_status_history(conn, claim_id, actor_id, claim["status"], new_status, justification, now)
        conn.execute(text("INSERT INTO history(claim_id, actor_id, action, note, created_at) VALUES (:claim,:actor,:action,:note,:at)"), {"claim": claim_id, "actor": actor_id, "action": "Statut administratif : " + new_status, "note": justification, "at": now})


def request_complement(engine, admin_id, claim_id, message):
    message = (message or "").strip()
    if len(message) < 5:
        raise ValueError("La demande de complément doit contenir au moins 5 caractères.")
    transition_status(engine, admin_id, claim_id, "En attente de complément", message)
    with engine.begin() as conn:
        owner = conn.execute(text("SELECT student_id FROM claims WHERE id=:id"), {"id": claim_id}).scalar_one()
        conn.execute(text("INSERT INTO administrative_complements(claim_id, requester_id, student_id, kind, message, created_at) VALUES (:claim,:actor,:student,'request',:message,:at)"), {"claim": claim_id, "actor": admin_id, "student": owner, "message": message, "at": utc_now()})


def respond_complement(engine, student_id, claim_id, message, filename=None, mime_type=None, payload=None):
    message = (message or "").strip()
    if len(message) < 3:
        raise ValueError("La réponse doit contenir au moins 3 caractères.")
    if filename and (payload is None or len(payload) > 5 * 1024 * 1024):
        raise ValueError("Pièce jointe invalide ou supérieure à 5 Mo.")
    with engine.begin() as conn:
        claim = conn.execute(text("SELECT student_id, status FROM claims WHERE id=:id"), {"id": claim_id}).mappings().first()
        _require_actor(conn, student_id, claim["student_id"] if claim else None, claim["student_id"] if claim else None)
        if not claim or claim["status"] != "En attente de complément":
            raise ValueError("Un complément n'est pas attendu pour cette réclamation.")
        now = utc_now()
        conn.execute(text("INSERT INTO administrative_complements(claim_id, requester_id, student_id, kind, message, original_filename, mime_type, payload, created_at) VALUES (:claim,:student,:student,'response',:message,:filename,:mime,:payload,:at)"), {"claim": claim_id, "student": student_id, "message": message, "filename": filename, "mime": mime_type, "payload": payload, "at": now})
        conn.execute(text("UPDATE claims SET status='Complément reçu', updated_at=:at WHERE id=:id"), {"at": now, "id": claim_id})
        append_status_history(conn, claim_id, student_id, "En attente de complément", "Complément reçu", message, now)
        conn.execute(text("INSERT INTO history(claim_id, actor_id, action, note, created_at) VALUES (:claim,:student,'Complément étudiant reçu',:message,:at)"), {"claim": claim_id, "student": student_id, "message": message, "at": now})


def administrative_agents(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, first_name, last_name FROM users WHERE role IN ('admin', 'agent') AND is_active=1 ORDER BY last_name, first_name")).mappings().all()
    return [dict(r) for r in rows]


def administrative_dashboard(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, status, service_id, assigned_agent_id, priority, due_at, created_at, resolved_at FROM claims")).mappings().all()
    claims = [dict(r) for r in rows]
    by_status = {}
    for claim in claims:
        by_status[claim["status"]] = by_status.get(claim["status"], 0) + 1
    urgent = sum(c["priority"] == "urgente" for c in claims if c["status"] not in ("Résolue", "Acceptée", "Rejetée", "Clôturée"))
    from administrative_deadlines import deadline_state
    states = [deadline_state(c) for c in claims]
    return {"total": len(claims), "by_status": by_status, "urgent": urgent,
            "overdue": states.count("En retard"), "near_due": states.count("Échéance proche")}
