"""Persisted v1.7.1 permission matrix and effective scopes."""
from sqlalchemy import text, inspect
from access_control import PERMISSIONS, ROLE_PERMISSIONS, ROLES
from administrative_workflow import utc_now

SCOPE_GLOBAL = "global"
SCOPE_DEPARTMENT = "department"
SCOPE_PROGRAM = "filiere"
SCOPE_SERVICE = "service"
SCOPE_ASSIGNED = "assigned"
SCOPE_OWN = "own"
SCOPES = (SCOPE_GLOBAL, SCOPE_DEPARTMENT, SCOPE_PROGRAM, SCOPE_SERVICE, SCOPE_ASSIGNED, SCOPE_OWN)

DEFAULT_SCOPES = {
    "claims.view_own": SCOPE_OWN, "claims.view_department": SCOPE_DEPARTMENT,
    "claims.view_program": SCOPE_PROGRAM, "claims.view_assigned": SCOPE_ASSIGNED,
    "claims.view_grade_complaints": SCOPE_DEPARTMENT,
    "claims.respond": SCOPE_ASSIGNED, "claims.request_information": SCOPE_ASSIGNED,
    "claims.assign": SCOPE_DEPARTMENT, "claims.change_status": SCOPE_ASSIGNED,
    "students.view": SCOPE_DEPARTMENT, "students.manage": SCOPE_DEPARTMENT,
    "users.create": SCOPE_GLOBAL, "users.manage": SCOPE_GLOBAL,
    "permissions.manage": SCOPE_GLOBAL, "audit.view": SCOPE_GLOBAL,
}


def initialize_permission_schema(engine):
    serial = "SERIAL PRIMARY KEY" if engine.dialect.name == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    statements = (
        f"CREATE TABLE IF NOT EXISTS role_permissions (id {serial}, role VARCHAR(50) NOT NULL, permission VARCHAR(100) NOT NULL, granted INTEGER NOT NULL DEFAULT 1, scope VARCHAR(30) NOT NULL DEFAULT 'global', UNIQUE(role, permission))",
        f"CREATE TABLE IF NOT EXISTS permission_matrix_history (id {serial}, actor_id INTEGER NOT NULL, action VARCHAR(50) NOT NULL, role VARCHAR(50), permission VARCHAR(100), scope VARCHAR(30), note TEXT NOT NULL DEFAULT '', created_at VARCHAR(50) NOT NULL)",
    )
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        for role in ROLES:
            for permission in PERMISSIONS:
                granted = int(permission in ROLE_PERMISSIONS.get(role, set()))
                scope = DEFAULT_SCOPES.get(permission, SCOPE_GLOBAL)
                conn.execute(text("INSERT INTO role_permissions(role,permission,granted,scope) SELECT :role,:permission,:granted,:scope WHERE NOT EXISTS (SELECT 1 FROM role_permissions WHERE role=:role AND permission=:permission)"),
                             {"role": role, "permission": permission, "granted": granted, "scope": scope})

        columns = {c["name"] for c in inspect(engine).get_columns("account_permissions")}
        if "scope" not in columns:
            conn.execute(text("ALTER TABLE account_permissions ADD COLUMN scope VARCHAR(30) NOT NULL DEFAULT 'global'"))


def role_permission_matrix(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT role, permission, granted, scope FROM role_permissions ORDER BY role, permission")).mappings().all()
    result = {}
    for row in rows:
        result.setdefault(row["role"], {})[row["permission"]] = {"granted": bool(row["granted"]), "scope": row["scope"]}
    return result


def save_role_permission_matrix(engine, actor_id, changes):
    """Save a validated matrix atomically. changes is permission -> {roles...}."""
    with engine.begin() as conn:
        role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": actor_id}).scalar()
        if role not in ("super_admin", "admin"):
            raise PermissionError("Seuls les administrateurs peuvent modifier la matrice des rôles.")
        if role == "admin" and "permissions.manage" not in _user_permissions(conn, actor_id):
            # Legacy admin compatibility is limited to account administration, not privilege escalation.
            raise PermissionError("Permission permissions.manage requise pour modifier les rôles.")
        for permission, role_values in changes.items():
            if permission not in PERMISSIONS:
                raise ValueError("Permission inconnue.")
            for role_name, granted_scope in role_values.items():
                if role_name not in ROLES:
                    raise ValueError("Rôle inconnu.")
                if isinstance(granted_scope, dict):
                    granted = int(bool(granted_scope.get("granted")))
                    scope = granted_scope.get("scope", DEFAULT_SCOPES.get(permission, SCOPE_GLOBAL))
                else:
                    granted, scope = (int(bool(granted_scope)), DEFAULT_SCOPES.get(permission, SCOPE_GLOBAL))
                if scope not in SCOPES:
                    raise ValueError("Périmètre inconnu.")
                if granted and permission in ("permissions.manage", "users.create") and role_name != "super_admin":
                    raise PermissionError("Ces privilèges ne peuvent être accordés qu'au super administrateur.")
                conn.execute(text("UPDATE role_permissions SET granted=:granted, scope=:scope WHERE role=:role AND permission=:permission"), {"granted": granted, "scope": scope, "role": role_name, "permission": permission})
                conn.execute(text("INSERT INTO permission_matrix_history(actor_id,action,role,permission,scope,note,created_at) VALUES(:actor,'permission_matrix_updated',:role,:permission,:scope,'Matrice des rôles modifiée',:at)"), {"actor": actor_id, "role": role_name, "permission": permission, "scope": scope, "at": utc_now()})
        conn.execute(text("UPDATE users SET permission_version=COALESCE(permission_version,0)+1 WHERE role IN (:r1,:r2,:r3,:r4,:r5,:r6)"), {"r1":"super_admin","r2":"admin","r3":"department_head","r4":"program_manager","r5":"administrative_agent","r6":"student"})


def restore_role_permissions(engine, actor_id, confirmed=False):
    if not confirmed:
        raise PermissionError("Confirmation requise pour restaurer les permissions par défaut.")
    with engine.begin() as conn:
        role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": actor_id}).scalar()
        if role != "super_admin":
            raise PermissionError("Seul le super administrateur peut restaurer la matrice.")
        for role_name in ROLES:
            for permission in PERMISSIONS:
                granted = int(permission in ROLE_PERMISSIONS.get(role_name, set()))
                scope = DEFAULT_SCOPES.get(permission, SCOPE_GLOBAL)
                conn.execute(text("UPDATE role_permissions SET granted=:granted, scope=:scope WHERE role=:role AND permission=:permission"), {"granted": granted, "scope": scope, "role": role_name, "permission": permission})
        conn.execute(text("UPDATE users SET permission_version=COALESCE(permission_version,0)+1"))
        conn.execute(text("INSERT INTO permission_matrix_history(actor_id,action,note,created_at) VALUES(:actor,'defaults_restored','Matrice restaurée, affectations conservées',:at)"), {"actor": actor_id, "at": utc_now()})


def _user_permissions(conn, actor_id):
    rows = conn.execute(text("SELECT permission FROM account_permissions WHERE user_id=:id AND granted=1"), {"id": actor_id}).mappings()
    return {row["permission"] for row in rows}
