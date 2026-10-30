"""Backend-controlled administrative status workflow for SQL-backed stores."""
from datetime import datetime, timezone

STATUSES_V16 = (
    "Soumise", "Affectée", "En cours", "En attente de complément",
    "Complément reçu", "Résolue", "Acceptée", "Rejetée", "Clôturée",
)

# Legacy statuses remain accepted for existing rows, but transitions are
# constrained once a claim enters the v1.6 workflow.
LEGACY_STATUSES = ("Soumise", "En cours", "Acceptée", "Rejetée", "Clôturée")

ALLOWED_TRANSITIONS = {
    "Soumise": {"Affectée", "En cours", "Acceptée", "Rejetée"},
    "Affectée": {"En cours", "En attente de complément", "Rejetée"},
    "En cours": {"En attente de complément", "Résolue", "Acceptée", "Rejetée"},
    "En attente de complément": {"Complément reçu", "Rejetée"},
    "Complément reçu": {"En cours", "Rejetée"},
    "Résolue": {"Acceptée", "Clôturée", "Rejetée", "En cours"},
    "Acceptée": {"Clôturée", "En cours"},
    "Rejetée": {"En cours"},
    "Clôturée": {"En cours"},
}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def validate_transition(old_status, new_status, justification="", is_assignee=False, is_admin=False):
    if old_status == new_status:
        return
    if old_status not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Statut existant non reconnu : {old_status}")
    if new_status not in STATUSES_V16:
        raise ValueError("Nouveau statut non reconnu.")
    if new_status not in ALLOWED_TRANSITIONS.get(old_status, set()):
        raise ValueError(f"Transition non autorisée : {old_status} → {new_status}")
    if old_status not in ("Soumise", "Affectée") and not (is_assignee or is_admin):
        raise PermissionError("Seul le responsable autorisé ou l'administrateur peut effectuer cette transition.")
    if new_status in ("Acceptée", "Rejetée", "Clôturée") and len((justification or "").strip()) < 10:
        raise ValueError("Une décision finale ou une clôture exige une justification de 10 caractères minimum.")


def append_status_history(conn, claim_id, actor_id, old_status, new_status, justification, at=None):
    conn.execute(__import__("sqlalchemy").text(
        "INSERT INTO administrative_status_history "
        "(claim_id, actor_id, old_status, new_status, justification, created_at) "
        "VALUES (:claim_id, :actor_id, :old_status, :new_status, :justification, :created_at)"
    ), {"claim_id": claim_id, "actor_id": actor_id, "old_status": old_status,
        "new_status": new_status, "justification": (justification or "")[:5000],
        "created_at": at or utc_now()})
