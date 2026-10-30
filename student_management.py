"""CSV et validation des fiches étudiants, indépendants de l'interface."""
import csv
import io
import re

COLUMNS = ("num_apogee", "nom_etudiant", "prenom", "email", "filiere", "niveau")
PROFILE_FIELDS = {"student_number", "last_name", "first_name", "email", "program", "level"}


def parse_csv_preview(csv_text):
    """Renvoie (lignes nettoyées, problèmes) ; n'insère jamais aucune donnée."""
    try:
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"), newline=""))
        headers = reader.fieldnames or []
        if not set(COLUMNS).issubset(headers):
            return [], ["Colonnes CSV obligatoires manquantes."]
        if "mot_de_passe" in headers or "password" in headers:
            return [], ["Les mots de passe en clair sont interdits."]
        rows = list(reader)
        if not 1 <= len(rows) <= 1000:
            return [], ["Le CSV doit contenir entre 1 et 1000 étudiants."]
        clean, issues, emails, apogees = [], [], set(), set()
        for no, row in enumerate(rows, 2):
            value = {k: (row.get(k) or "").strip() for k in COLUMNS}
            value["email"] = value["email"].lower()
            if any(not v or len(v) > 200 for v in value.values()):
                issues.append(f"Ligne {no} : champ vide ou supérieur à 200 caractères.")
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value["email"]):
                issues.append(f"Ligne {no} : adresse e-mail invalide.")
            if value["email"] in emails or value["num_apogee"] in apogees:
                issues.append(f"Ligne {no} : doublon interne (e-mail ou APOGÉE).")
            emails.add(value["email"])
            apogees.add(value["num_apogee"])
            clean.append(value)
        return clean, issues
    except csv.Error:
        return [], ["Le fichier CSV est mal formé."]


def normalize_profile(updates):
    if not isinstance(updates, dict) or not updates or not set(updates).issubset(PROFILE_FIELDS | {"is_active"}):
        raise ValueError("Champs de fiche non autorisés.")
    clean = {}
    for key, val in updates.items():
        if key == "is_active":
            if not isinstance(val, bool):
                raise ValueError("Le statut actif/inactif doit être booléen.")
            clean[key] = val
            continue
        if not isinstance(val, str):
            raise ValueError("Chaque champ doit être du texte.")
        value = val.strip()
        if not 1 <= len(value) <= 200:
            raise ValueError("Champ obligatoire : 1 à 200 caractères.")
        clean[key] = value.lower() if key == "email" else value
    if "email" in clean and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean["email"]):
        raise ValueError("Adresse e-mail invalide.")
    return clean


def public_student(data):
    return {k: data.get(k) for k in ("id", "student_number", "last_name",
                                     "first_name", "email", "program", "level", "is_active")}
