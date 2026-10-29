"""Messages de diagnostic PostgreSQL sans divulgation d'identifiants.

Jamais inclure str(exception), les arguments d'une DBAPIError ou DATABASE_URL
dans la page publique : certains drivers exposent l'URI et ses secrets.
"""
from sqlalchemy.exc import ArgumentError, OperationalError, ProgrammingError


class DatabaseSetupError(RuntimeError):
    """Échec dans une phase déterminée de l'initialisation."""

    def __init__(self, phase):
        self.phase = phase
        super().__init__(phase)


def safe_database_diagnostic(exc):
    """Renvoie un code et une explication statiques, sans valeurs utilisateur."""
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    messages = {
        "28P01": ("DB-AUTH", "Identifiant ou mot de passe PostgreSQL refusé. Recopiez l'URL de connexion officielle de votre fournisseur."),
        "28000": ("DB-AUTH", "Accès PostgreSQL refusé. Vérifiez l'utilisateur et ses autorisations."),
        "3D000": ("DB-NAME", "La base indiquée dans DATABASE_URL n'existe pas."),
        "42501": ("DB-PERM", "L'utilisateur PostgreSQL ne peut pas créer ou consulter les tables nécessaires."),
        "42P01": ("DB-SCHEMA", "Une table requise manque. Vérifiez le schéma et l'initialisation de la base."),
        "57P03": ("DB-SLEEP", "La base PostgreSQL est temporairement indisponible ou en cours de démarrage."),
    }
    if state in messages:
        return messages[state]
    if isinstance(state, str) and state.startswith("08"):
        return ("DB-NETWORK", "Connexion réseau à PostgreSQL impossible. Vérifiez l'hôte, le port, le pare-feu et le statut du service.")
    if isinstance(exc, (ValueError, ArgumentError)):
        return ("DB-URL", "Format DATABASE_URL ou paramètres d'initialisation invalides. Vérifiez les valeurs et les caractères spéciaux du mot de passe.")
    if isinstance(exc, OperationalError):
        return ("DB-CONN", "PostgreSQL est inaccessible ou refuse la connexion. Vérifiez l'URL, l'option SSL et les autorisations réseau.")
    if isinstance(exc, ProgrammingError):
        return ("DB-SQL", "Erreur de création ou d'accès aux tables. Vérifiez les droits CREATE et le schéma PostgreSQL.")
    return ("DB-OTHER", "Erreur d'initialisation. Consultez les Cloud logs pour le diagnostic serveur, sans diffuser les secrets.")
