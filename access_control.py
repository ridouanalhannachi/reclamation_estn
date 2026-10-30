"""Backend-first RBAC and resource-scope authorization for ESTN."""
from sqlalchemy import text

PERMISSIONS = (
    "claims.view_own", "claims.view_department", "claims.view_program",
    "claims.view_assigned", "claims.view_grade_complaints", "claims.respond",
    "claims.request_information", "claims.assign", "claims.change_status",
    "students.view", "students.manage", "users.create", "users.manage",
    "permissions.manage", "audit.view",
)
ROLES = ("super_admin", "admin", "department_head", "program_manager",
         "administrative_agent", "student")

ROLE_PERMISSIONS = {
    "super_admin": set(PERMISSIONS),
    "admin": {p for p in PERMISSIONS if p != "permissions.manage" and p != "users.create"},
    "department_head": {"claims.view_department", "claims.view_grade_complaints", "claims.respond",
                        "claims.request_information", "claims.assign", "claims.change_status",
                        "students.view", "audit.view"},
    "program_manager": {"claims.view_program", "claims.view_grade_complaints", "claims.respond",
                        "claims.request_information", "claims.change_status", "students.view"},
    "administrative_agent": {"claims.view_assigned", "claims.respond", "claims.request_information",
                             "claims.change_status", "students.view"},
    "student": {"claims.view_own"},
}


def initialize_rbac(conn):
    conn.execute(text("CREATE TABLE IF NOT EXISTS account_departments (user_id INTEGER NOT NULL, department_id INTEGER NOT NULL, PRIMARY KEY(user_id, department_id))"))


def actor_permissions(conn, user_id):
    role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": user_id}).scalar()
    if not role:
        return set()
    built_in = set(ROLE_PERMISSIONS.get(role, set()))
    try:
        configured = conn.execute(text("SELECT permission FROM role_permissions WHERE role=:role AND granted=1"), {"role": role}).scalars().all()
        if configured:
            built_in = set(configured)
    except Exception:
        pass
    explicit = {r[0] for r in conn.execute(text(
        "SELECT permission FROM account_permissions WHERE user_id=:id AND granted=1"), {"id": user_id})}
    revoked = {r[0] for r in conn.execute(text(
        "SELECT permission FROM account_permissions WHERE user_id=:id AND granted=0"), {"id": user_id})}
    return (built_in | explicit) - revoked


def has_permission(conn, user_id, permission):
    return permission in actor_permissions(conn, user_id) and permission in PERMISSIONS


def require_permission(conn, user_id, permission):
    if not has_permission(conn, user_id, permission):
        raise PermissionError("Autorisation refusée.")


def scoped_departments(conn, user_id):
    rows = conn.execute(text("SELECT department_id FROM account_departments WHERE user_id=:id"), {"id": user_id}).scalars().all()
    return {int(v) for v in rows}


def scoped_programs(conn, user_id):
    rows = conn.execute(text("SELECT program_id FROM account_programs WHERE user_id=:id"), {"id": user_id}).scalars().all()
    return {int(v) for v in rows}


def can_view_claim(conn, user_id, claim, permission_context=False):
    role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": user_id}).scalar()
    if not role:
        return False
    if role == "super_admin":
        return True
    if role == "admin":
        return True
    if role == "student":
        return claim["student_id"] == user_id if has_permission(conn, user_id, "claims.view_own") else False
    if claim.get("id") is None:
        return False
    if has_permission(conn, user_id, "claims.view_assigned") and claim.get("assigned_agent_id") == user_id:
        return True
    if has_permission(conn, user_id, "claims.view_department"):
        depts = scoped_departments(conn, user_id)
        if claim.get("responsible_department_id") in depts:
            return True
    if has_permission(conn, user_id, "claims.view_program"):
        programs = scoped_programs(conn, user_id)
        if claim.get("responsible_program_id") in programs:
            return True
    if has_permission(conn, user_id, "claims.view_grade_complaints") and claim.get("is_grade_complaint"):
        return claim.get("responsible_department_id") in scoped_departments(conn, user_id)
    return False


def require_claim_access(conn, user_id, claim):
    if not can_view_claim(conn, user_id, claim):
        raise PermissionError("Accès à cette réclamation refusé.")


def permission_scopes(conn, user_id):
    """Return effective configured scopes keyed by permission."""
    permissions = actor_permissions(conn, user_id)
    role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": user_id}).scalar()
    if not role:
        return {}
    scopes = {}
    for permission in permissions:
        scope = conn.execute(text("SELECT scope FROM role_permissions WHERE role=:role AND permission=:permission AND granted=1"), {"role": role, "permission": permission}).scalar()
        if scope:
            scopes[permission] = scope
    return scopes


def permission_version(conn, user_id):
    return conn.execute(text("SELECT COALESCE(permission_version, 0) FROM users WHERE id=:id"), {"id": user_id}).scalar_one_or_none() or 0
