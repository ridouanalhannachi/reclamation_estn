"""ESTN department and program reference data and SQL scope management."""
from sqlalchemy import inspect, text


def initialize_departments(engine):
    dialect = engine.dialect.name
    serial = "SERIAL PRIMARY KEY" if dialect == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"
    statements = [
        f"CREATE TABLE IF NOT EXISTS departments (id {serial}, name VARCHAR(200) NOT NULL UNIQUE, code VARCHAR(50), is_active INTEGER NOT NULL DEFAULT 1)",
        f"CREATE TABLE IF NOT EXISTS programs (id {serial}, department_id INTEGER NOT NULL, name VARCHAR(200) NOT NULL, code VARCHAR(50), level VARCHAR(100), is_active INTEGER NOT NULL DEFAULT 1, UNIQUE(department_id, name))",
        "CREATE TABLE IF NOT EXISTS account_departments (user_id INTEGER NOT NULL, department_id INTEGER NOT NULL, PRIMARY KEY(user_id, department_id))",
        "CREATE TABLE IF NOT EXISTS account_programs (user_id INTEGER NOT NULL, program_id INTEGER NOT NULL, PRIMARY KEY(user_id, program_id))",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def list_departments(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, name, code FROM departments WHERE is_active=1 ORDER BY name")).mappings().all()
    return [dict(r) for r in rows]


def create_department(engine, actor_id, name, code=""):
    name = (name or "").strip()
    if len(name) < 2:
        raise ValueError("Nom de département invalide.")
    with engine.begin() as conn:
        role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": actor_id}).scalar()
        if role not in ("super_admin", "admin"):
            raise PermissionError("Seuls les administrateurs peuvent gérer les départements.")
        conn.execute(text("INSERT INTO departments(name, code) SELECT :name, :code WHERE NOT EXISTS (SELECT 1 FROM departments WHERE lower(name)=lower(:name))"), {"name": name, "code": (code or "")[:50]})


def create_program(engine, actor_id, department_id, name, code="", level=""):
    if len((name or "").strip()) < 2:
        raise ValueError("Nom de filière invalide.")
    with engine.begin() as conn:
        role = conn.execute(text("SELECT role FROM users WHERE id=:id AND is_active=1"), {"id": actor_id}).scalar()
        if role not in ("super_admin", "admin"):
            raise PermissionError("Seuls les administrateurs peuvent gérer les filières.")
        if not conn.execute(text("SELECT 1 FROM departments WHERE id=:id AND is_active=1"), {"id": department_id}).first():
            raise ValueError("Département introuvable.")
        conn.execute(text("INSERT INTO programs(department_id, name, code, level) SELECT :department,:name,:code,:level WHERE NOT EXISTS (SELECT 1 FROM programs WHERE department_id=:department AND lower(name)=lower(:name))"),
                     {"department": department_id, "name": name.strip(), "code": (code or "")[:50], "level": (level or "")[:100]})


def list_programs(engine, department_id=None):
    sql = "SELECT id, department_id, name, code, level FROM programs WHERE is_active=1"
    params = {}
    if department_id is not None:
        sql += " AND department_id=:department_id"
        params["department_id"] = department_id
    with engine.connect() as conn:
        rows = conn.execute(text(sql + " ORDER BY name"), params).mappings().all()
    return [dict(r) for r in rows]
