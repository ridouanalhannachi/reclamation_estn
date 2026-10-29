"""Configure Google Drive OAuth and generate REAL Streamlit secrets locally.

This script writes the private values to a file outside the Git repository.
It NEVER sends any OAuth token or password to GitHub/ChatGPT.
Google Drive API must be enabled; OAuth client type: Desktop app.
"""
import argparse
import base64
import json
import re
import secrets
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = "https://www.googleapis.com/auth/drive.appdata"
DEFAULT_OUTPUT = Path.home() / "reclamation-estn-private" / "streamlit-secrets.toml"


def build_secrets(authorized_user_json, admin_email, admin_password):
    """Return TOML whose values come from the user's real Google authorization."""
    email = (admin_email or "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Adresse e-mail administrateur invalide.")
    if not admin_password or len(admin_password) < 20:
        raise ValueError("Le mot de passe initial doit contenir au moins 20 caractères.")
    credentials = json.loads(authorized_user_json)
    required = ("refresh_token", "client_id", "client_secret", "token_uri")
    if any(not credentials.get(key) for key in required):
        raise ValueError("Autorisation OAuth incomplète : un nouveau refresh token est nécessaire.")
    if SCOPE not in (credentials.get("scopes") or []):
        raise ValueError("Scope drive.appdata absent du jeton Google.")
    encoded = base64.b64encode(
        json.dumps(credentials, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return (
        'GDRIVE_TOKEN_B64 = ' + json.dumps(encoded) + "\n"
        'BOOTSTRAP_ADMIN_EMAIL = ' + json.dumps(email) + "\n"
        'BOOTSTRAP_ADMIN_PASSWORD = ' + json.dumps(admin_password) + "\n"
    )


def save_private_secrets(output, content):
    """Fail if the file already exists: never overwrite existing credentials."""
    path = Path(output).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create avoids accidental replacement or truncation.
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Générer sur VOTRE PC les vraies valeurs Streamlit/Google Drive, sans les publier."
    )
    parser.add_argument(
        "--client-secret", required=True, type=Path,
        help="Chemin vers le nouveau JSON OAuth Google Cloud de type Desktop app."
    )
    parser.add_argument(
        "--admin-email",
        help="E-mail du premier administrateur (demandé si non fourni)."
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="Destination du fichier privé de secrets, hors du dépôt Git."
    )
    args = parser.parse_args()
    if not args.client_secret.is_file():
        parser.error("Le fichier OAuth Google client_secret.json est introuvable.")
    email = args.admin_email or input("E-mail du compte administrateur ESTN : ").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        parser.error("Adresse e-mail administrateur invalide.")
    if args.output.expanduser().exists():
        parser.error("Le fichier de destination existe déjà ; aucun secret ne sera écrasé.")

    print("Le navigateur va ouvrir une autorisation Google Drive (scope drive.appdata).")
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), scopes=[SCOPE])
    credentials = flow.run_local_server(
        port=0, prompt="consent", access_type="offline"
    )
    if not credentials.refresh_token:
        raise RuntimeError(
            "Google n'a pas donné de refresh token. Vérifiez le statut OAuth et révoquez "
            "la précédente autorisation, puis recommencez."
        )

    initial_password = secrets.token_urlsafe(30)
    secret_text = build_secrets(credentials.to_json(), email, initial_password)
    output = save_private_secrets(args.output, secret_text)

    print("\nConfiguration créée et enregistrée LOCALEMENT :")
    print(output)
    print("1. Ouvrez le fichier sur votre PC et copiez toutes ses lignes.")
    print("2. Streamlit Cloud → application → Settings → Secrets → collez → Save.")
    print("3. Connectez-vous avec l'e-mail administrateur choisi.")
    print("4. Votre mot de passe initial est dans ce fichier, pas dans le terminal.")
    print("5. Changez le mot de passe à la première connexion.")
    print("Ne publiez et ne partagez JAMAIS ce fichier, même sous forme Base64.")


if __name__ == "__main__":
    main()
