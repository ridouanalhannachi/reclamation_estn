"""Controlled account creation and assignment of RBAC scopes."""
import secrets
import string
from sqlalchemy import text
from werkzeug.security import generate_password_hash
from access_control import PERMISSIONS, ROLES, require_permission, scoped_departments, scoped_programs
from administrative_workflow import utc_now


def _require_users_admin(conn, actor_id):
    role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": actor_id}).scalar()
    if role in ("super_admin", "admin"):
        return
    require_permission(conn, actor_id, "users.create")


def temporary_password(length=20):
    alphabet = string.ascii_letters + string.digits + "!@#%&*_-"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in value) and any(c.isupper() for c in value)
                and any(c.isdigit() for c in value) and any(c in "!@#%&*_-" for c in value)):
            return value


def initialize_account_schema(engine):
    dialect = engine.dialect.name
    pid = "SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    statements = [
        f"CREATE TABLE IF NOT EXISTS account_permissions (id {pid}, user_id INTEGER NOT NULL, permission VARCHAR(100) NOT NULL, granted INTEGER NOT NULL, UNIQUE(user_id, permission))",
        f"CREATE TABLE IF NOT EXISTS account_permission_history (id {pid}, actor_id INTEGER NOT NULL, target_user_id INTEGER NOT NULL, action VARCHAR(50) NOT NULL, permission VARCHAR(100), note TEXT NOT NULL DEFAULT '', created_at VARCHAR(50) NOT NULL)",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def create_account(engine, actor_id, first_name, last_name, email, role, department_ids=(), program_ids=(), permissions=(), return_temp_password=False):
    if role not in ROLES or role == "student":
        raise ValueError("Rôle de compte utilisateur invalide.")
    first_name, last_name, email = (first_name or "").strip(), (last_name or "").strip(), (email or "").strip().lower()
    if not first_name or not last_name or "@" not in email:
        raise ValueError("Nom, prénom et e-mail valides requis.")
    if any(p not in PERMISSIONS for p in permissions):
        raise ValueError("Permission inconnue.")
    password = temporary_password()
    with engine.begin() as conn:
        _require_users_admin(conn, actor_id)
        user_id = conn.execute(text("SELECT id FROM users WHERE lower(email)=:email"), {"email": email}).scalar()
        if user_id:
            raise ValueError("Un compte existe déjà avec cet e-mail.")
        result = conn.execute(text("INSERT INTO users(student_number,last_name,first_name,email,program,level,password_hash,password_change_required,role,is_active,permission_version) VALUES(NULL,:last,:first,:email,'','',:password_hash,1,:role,1,0)"),
                              {"last": last_name, "first": first_name, "email": email,
                               "password_hash": generate_password_hash(password), "role": role})
        user_id = result.lastrowid
        for department_id in set(department_ids):
            conn.execute(text("INSERT INTO account_departments(user_id, department_id) SELECT :user,:dept WHERE EXISTS (SELECT 1 FROM departments WHERE id=:dept AND is_active=1)"), {"user": user_id, "dept": department_id})
        for program_id in set(program_ids):
            conn.execute(text("INSERT INTO account_programs(user_id, program_id) SELECT :user,:program WHERE EXISTS (SELECT 1 FROM programs WHERE id=:program AND is_active=1)"), {"user": user_id, "program": program_id})
        for permission in set(permissions):
            conn.execute(text("INSERT INTO account_permissions(user_id, permission, granted) VALUES (:user,:permission,1)"), {"user": user_id, "permission": permission})
        conn.execute(text("INSERT INTO account_permission_history(actor_id,target_user_id,action,note,created_at) VALUES (:actor,:target,'account_created',:note,:at)"),
                     {"actor": actor_id, "target": user_id, "note": "Création de compte et affectations initiales", "at": utc_now()})
    return user_id, password if return_temp_password else None


def set_account_permissions(engine, actor_id, target_user_id, role=None, department_ids=(), program_ids=(), granted=(), revoked=(), active=None):
    if any(p not in PERMISSIONS for p in granted + revoked):
        raise ValueError("Permission inconnue.")
    with engine.begin() as conn:
        role_actor = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": actor_id}).scalar()
        if role_actor not in ("super_admin", "admin") and not __import__("access_control", fromlist=["has_permission"]).has_permission(conn, actor_id, "permissions.manage"):
            raise PermissionError("Gestion des permissions refusée.")
        target_role = conn.execute(text("SELECT role FROM users WHERE id=:id"), {"id": target_user_id}).scalar()
        if not target_role:
            raise LookupError("Compte introuvable.")
        if role and role in ROLES:
            conn.execute(text("UPDATE users SET role=:role WHERE id=:id"), {"role": role, "id": target_user_id})
        if active is not None:
            conn.execute(text("UPDATE users SET is_active=:active WHERE id=:id"), {"active": int(bool(active)), "id": target_user_id})
        conn.execute(text("DELETE FROM account_departments WHERE user_id=:id"), {"id": target_user_id})
        conn.execute(text("DELETE FROM account_programs WHERE user_id=:id"), {"id": target_user_id})
        for department_id in set(department_ids):
            conn.execute(text("INSERT INTO account_departments(user_id,department_id) SELECT :user,:dept WHERE EXISTS (SELECT 1 FROM departments WHERE id=:dept AND is_active=1)"), {"user": target_user_id, "dept": department_id})
        for program_id in set(program_ids):
            conn.execute(text("INSERT INTO account_programs(user_id,program_id) SELECT :user,:program WHERE EXISTS (SELECT 1 FROM programs WHERE id=:program AND is_active=1)"), {"user": target_user_id, "program": program_id})
        conn.execute(text("DELETE FROM account_permissions WHERE user_id=:id"), {"id": target_user_id})
        for permission in set(granted):
            conn.execute(text("INSERT INTO account_permissions(user_id,permission,granted) VALUES (:id,:permission,1)"), {"id": target_user_id, "permission": permission})
        for permission in set(revoked):
            conn.execute(text("INSERT INTO account_permissions(user_id,permission,granted) VALUES (:id,:permission,0)"), {"id": target_user_id, "permission": permission})
        conn.execute(text("UPDATE users SET permission_version=COALESCE(permission_version,0)+1 WHERE id=:id"), {"id": target_user_id})
        conn.execute(text("INSERT INTO account_permission_history(actor_id,target_user_id,action,note,created_at) VALUES (:actor,:target,'permissions_updated',:note,:at)"),
                     {"actor": actor_id, "target": target_user_id, "note": "Rôle, périmètres ou permissions modifiés", "at": utc_now()})
