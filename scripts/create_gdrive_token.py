"""Generate NEW Google Drive OAuth credentials outside the Git repository.

Usage (on user's own PC):
  python scripts/create_gdrive_token.py --client-secret "C:\secure\client_secret.json"

Google Cloud project: Drive API enabled; OAuth desktop client;
authorized scope: drive.appdata. Do not share the resulting private file.
"""
import argparse
import base64
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = "https://www.googleapis.com/auth/drive.appdata"


def main():
    parser = argparse.ArgumentParser(description="Créer un jeton OAuth Drive neuf pour Réclamations ESTN.")
    parser.add_argument("--client-secret", required=True, type=Path,
                        help="Fichier client_secret.json téléchargé depuis Google Cloud.")
    args = parser.parse_args()
    if not args.client_secret.is_file():
        parser.error("Fichier client_secret.json introuvable.")
    flow = InstalledAppFlow.from_client_secrets_file(str(args.client_secret), scopes=[SCOPE])
    credentials = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not credentials.refresh_token:
        raise RuntimeError("Google n'a pas retourné de refresh token. Révoquez l'autorisation précédente, puis recommencez.")
    payload = json.loads(credentials.to_json())
    output = Path.home() / "reclamation-estn-private" / "gdrive-secrets.toml"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(
            "Un fichier privé de secrets existe déjà. Ne pas l'écraser automatiquement : "
            + str(output)
        )
    encoded = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    output.write_text('GDRIVE_TOKEN_B64 = "' + encoded + '"\n', encoding="utf-8")
    try:
        output.chmod(0o600)
    except OSError:
        pass
    print("Jeton écrit dans un fichier PRIVÉ hors du dépôt :", output)
    print("Copier sa ligne GDRIVE_TOKEN_B64 dans Streamlit → Settings → Secrets.")
    print("Ne partager ni le fichier ni les identifiants avec une autre personne.")


if __name__ == "__main__":
    main()
