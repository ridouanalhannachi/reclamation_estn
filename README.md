# Réclamations ESTN

Application universitaire pour le dépôt, le suivi et le traitement des réclamations. Première version fonctionnelle issue d'un dépôt initialement vide : **v1.0 – socle sécurisé**.

## Fonctionnalités v1.0

- Authentification par e-mail et mot de passe haché (Werkzeug).
- Deux rôles : étudiant et administrateur ; séparation des droits et des données.
- Dépôt de réclamation (catégorie, objet, description, pièce jointe PDF/PNG/JPEG limitée à 5 Mo).
- Consultation individuelle, téléchargement privé des pièces jointes, historique horodaté.
- Tableau de bord administrateur, filtres et décision motivée ; cinq statuts.
- Importation CSV atomique (tout ou rien), détection des doublons, génération de mots de passe uniques aléatoires.
- Obligation de changer le mot de passe temporaire à la première connexion.
- Jetons CSRF sur toutes les actions POST, requêtes SQL paramétrées, cookie HttpOnly et SameSite, en-têtes de sécurité.
- Interface **Academic Blue** claire et responsive, sans dépendance à un CDN.

> **Attention :** cette version n'est pas encore homologuée pour un déploiement public. Prévoir un audit, un durcissement contre les tentatives de connexion répétées, une gestion opérationnelle des sauvegardes et une politique de conservation des données.

## Installation (Windows 11 / PowerShell)

Exige Python 3.10+.

    git clone https://github.com/ridouanalhannachi/reclamation_estn.git
    cd reclamation_estn
    py -3 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    $env:ESTN_SECRET_KEY = py -c "import secrets; print(secrets.token_hex(32))"
    flask --app app init-db
    flask --app app create-admin
    flask --app app run --host 127.0.0.1 --port 5000

Ouvrir http://127.0.0.1:5000. À la création du compte administrateur, renseigner l'e-mail, le nom, le prénom et un mot de passe d'au moins 12 caractères. La commande ne crée **aucun compte par défaut**.

Dans la commande de génération de la clé, si PowerShell réagit mal à l'affectation, exécuter séparément :

    py -c "import secrets; print(secrets.token_hex(32))"

Puis définir la variable d'environnement avec la clé affichée. **Ne jamais versionner une clé réelle.** Sur un serveur, la clé doit être persistante entre les redémarrages et stockée en dehors du dépôt.

## Installation (Linux / macOS)

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    export ESTN_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    flask --app app init-db
    flask --app app create-admin
    flask --app app run --host 127.0.0.1 --port 5000

Ne pas utiliser le serveur de développement Flask sur Internet. Pour un déploiement, configurer un serveur WSGI, HTTPS, les autorisations des fichiers et ESTN_HTTPS=1 (cookies Secure) derrière une terminaison HTTPS appropriée.

## Importation des étudiants

Dans l'administration, ouvrir **Ajouter des étudiants**, puis fournir un fichier CSV UTF-8 avec ces colonnes (exactement celles-ci pour le modèle recommandé) :

    num_apogee,nom_etudiant,prenom,email,filiere,niveau
    A12345,EL AMRANI,Youssef,youssef@exemple.ma,Génie Informatique,DUT 2
    A12346,BENNANI,Salma,salma@exemple.ma,Réseaux et Télécommunications,DUT 1

L'ancienne colonne mot_de_passe est volontairement rejetée. Chaque compte reçoit un mot de passe aléatoire, haché en base. Un fichier CSV des **identifiants temporaires est téléchargé une seule fois** : le conserver et le transmettre par canal sécurisé, sans l'ajouter au dépôt. Un changement de mot de passe est imposé à la première connexion.

Les doublons, les champs manquants, les e-mails invalides et les fichiers dépassant 1000 lignes annulent tout l'import.

## Conservation des données

- Base : instance/estn.sqlite3
- Pièces jointes : instance/uploads/
- Ces fichiers ne sont **jamais** versionnés par Git (.gitignore).
- Sauvegarder ensemble la base de données et les pièces jointes hors du dépôt.
- Les anciennes captures d'écran ne sont pas des données réelles du dépôt : aucun étudiant ou réclamation n'est préchargé.

## Tests

    python -m unittest discover -s tests -v

Les tests CI s'exécutent également via GitHub Actions sur chaque branche de fonctionnalité et pull request.

## Prochaines itérations

- **v1.1 :** durcissement de l'authentification (limitation des tentatives, récupération de compte contrôlée, journal d'audit étendu).
- **v1.2 :** traitement par services, affectation, circuit décisionnel et délais de réponse.
- **v1.3 :** notifications et suivi étudiant enrichi.
- **v1.4 :** statistiques, pagination, export, tableaux de bord avancés.

Workflow : développer chaque évolution sur une branche dédiée, exécuter les tests, ouvrir une PR, intégrer sans écraser les données.


---

## Déploiement sur Streamlit Community Cloud (v1.1)

La version Flask reste disponible dans `app.py` ; l'interface dédiée à Streamlit se trouve dans **`streamlit_app.py`**, avec un backend SQLAlchemy dans `streamlit_store.py`. Aucun étudiant, mot de passe ou fichier personnel n'est inclus dans Git.

### 1. Base de données PostgreSQL persistante (obligatoire)

Créez une base PostgreSQL hébergée auprès d'un fournisseur de confiance (par exemple Neon ou Supabase) avant de déployer. Copiez son URL de connexion **avec SSL**. Pour la sécurité des données étudiantes, configurez les sauvegardes et la résidence des données appropriées avec votre établissement.

**Important :** Streamlit Community Cloud peut supprimer les fichiers locaux lors d'un redémarrage ou redéploiement. Il ne faut jamais utiliser SQLite ni stocker des pièces jointes sur disque pour les vraies réclamations sur Community Cloud. Les pièces jointes de cette version Streamlit sont conservées dans PostgreSQL (taille maximale 5 Mo par fichier).

### 2. Installer et déployer depuis GitHub

1. Ouvrir https://share.streamlit.io/ et se connecter au compte GitHub autorisé sur le dépôt privé.
2. Cliquer sur **Create app** puis **Yup, I have an app**.
3. Renseigner les champs :
   - Repository : `ridouanalhannachi/reclamation_estn`
   - Branch : `main`
   - Main file path : `streamlit_app.py`
   - Python : 3.12 (si disponible)
4. Sous **Advanced settings → Secrets**, ajouter **sans committer dans GitHub** :

   ```toml
   DATABASE_URL = "postgresql+psycopg://UTILISATEUR:MOT_DE_PASSE@HOTE:5432/NOM_BD?sslmode=require"
   BOOTSTRAP_ADMIN_EMAIL = "admin@votre-etablissement.ma"
   BOOTSTRAP_ADMIN_PASSWORD = "CHOISIR_UN_MOT_DE_PASSE_ALEATOIRE_FORT_DE_20_CARACTERES"
   ```

   Ce sont des exemples : remplacer par les vraies valeurs. Les mots de passe contenant des caractères spéciaux doivent être encodés dans l'URL SQL selon les règles d'encodage URL.

5. Cliquer sur **Deploy**. À la première connexion administrateur, le changement du mot de passe temporaire est obligatoire.
6. Dans **Settings → Sharing**, limiter l'accès aux personnes autorisées pendant les tests. L'application a aussi son propre système de connexion par rôle.

La console Streamlit peut demander d'accorder l'accès au dépôt GitHub privé. Ne rendez pas le dépôt public pour contourner ce problème.

### 3. Synchronisation automatique GitHub → Streamlit

Quand cette PR est fusionnée dans `main` et que l'application a été créée sur Community Cloud, les prochains commits sur `main` sont automatiquement détectés. Aucun push spécial vers Streamlit n'est nécessaire.

**Attention : synchronisation du code ≠ synchronisation des données.** La base PostgreSQL garde les réclamations indépendamment des commits. Les bases SQLite locales n'ont pas été transférées vers PostgreSQL : si une ancienne installation contient des données réelles, prévoir une migration contrôlée et sauvegardée.

### 4. Tester sur un poste Windows 11

La même URL PostgreSQL peut être passée localement via la variable `DATABASE_URL`. Pour expérimenter avec une base SQLite temporaire **uniquement en local** :

```powershell
git clone https://github.com/ridouanalhannachi/reclamation_estn.git
cd reclamation_estn
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
New-Item -ItemType Directory -Force instance | Out-Null
$env:DATABASE_URL = "sqlite:///instance/estn.sqlite3"
$env:ESTN_ALLOW_LOCAL_SQLITE = "1"
$env:BOOTSTRAP_ADMIN_EMAIL = "admin@example.org"
$env:BOOTSTRAP_ADMIN_PASSWORD = "Remplacez-Ce-Mot-De-Passe-Long"
streamlit run streamlit_app.py
```

Ouvrir l'adresse indiquée par Streamlit (en général http://localhost:8501). Le mode local SQLite est une option de développement, pas un mode de production.

### 5. Restrictions de la version

- Première version Streamlit : pas encore de limitation globale des tentatives de connexion, de 2FA, de notifications e-mail ou de procédure administrée de récupération des comptes.
- Les traitements doivent être testés sur un jeu de données fictives avant d'utiliser des données étudiantes réelles.
- Les droits de lecture sont vérifiés **dans les requêtes backend**, non seulement par les pages affichées.
- Avant la production : audits sécurité/confidentialité, supervision, sauvegardes PostgreSQL, politique RGPD/loi marocaine 09-08 et conventions avec le fournisseur d'hébergement.

### Dépannage : « Impossible de préparer la base PostgreSQL »

À partir du correctif **v1.1.1**, un code court est affiché sans révéler l'URL ou les mots de passe.

| Code | Vérification |
| --- | --- |
| `DB-URL` | `DATABASE_URL` complète ; format PostgreSQL ; URL officielle copiée depuis le fournisseur ; caractères spéciaux encodés dans les mots de passe |
| `DB-AUTH` | Vérifier le nom d'utilisateur et le mot de passe PostgreSQL |
| `DB-NAME` | Vérifier le nom de la base |
| `DB-PERM` | Autoriser les opérations `CREATE TABLE` sur le schéma choisi |
| `DB-NETWORK` / `DB-CONN` | Vérifier l'hôte, le port, la disponibilité de la base et `sslmode=require` |
| `DB-SLEEP` | Vérifier le démarrage et la disponibilité de la base |
| `DB-SQL` / `DB-SCHEMA` | Examiner les droits SQL et le schéma (ne pas supprimer les tables existantes) |
| `DB-OTHER` | Consulter les logs Streamlit sans partager les secrets |

Dans **https://share.streamlit.io → ⋯ → Settings → Secrets**, définir les valeurs à la racine TOML, **sans** en-tête `[connections.*]` :

```toml
DATABASE_URL = "postgresql://UTILISATEUR:MOT_DE_PASSE@HOTE/NOM_BASE?sslmode=require"
BOOTSTRAP_ADMIN_EMAIL = "admin@exemple.ma"
BOOTSTRAP_ADMIN_PASSWORD = "CHOISIR_UN_MOT_DE_PASSE_INITIAL_TRES_FORT"
```

**Les valeurs ci-dessus sont des exemples qui ne permettent pas de se connecter.** Créer une base PostgreSQL réelle auprès d'un fournisseur, puis utiliser sa chaîne de connexion. Les mots de passe d'administration sont créés seulement à l'initialisation d'une base sans administrateur ; modifier ensuite la variable dans Secrets **ne réinitialise pas** le compte administrateur existant.

Ne jamais poster la vraie chaîne `DATABASE_URL`, une capture de Secrets ou un log contenant des identifiants. Vérifier d'abord les codes sûrs affichés dans l'application.

---

## v1.2 — Streamlit avec Google Drive (sans PostgreSQL)

Google Drive remplace PostgreSQL dès que le secret GDRIVE_TOKEN_B64 est présent. Les comptes, réclamations, pièces jointes et décisions sont alors stockés dans le dossier privé appDataFolder. Aucune base DATABASE_URL n'est requise dans ce mode.

**Important : le jeton OAuth communiqué précédemment est exposé. Révoquez l'ancienne autorisation Google et ne réutilisez jamais ce jeton.** Base64 n'est pas du chiffrement.

### 1. Préparer un nouveau client Google OAuth

1. Ouvrir https://console.cloud.google.com/ ; créer ou choisir un projet.
2. Activer Google Drive API via APIs & Services → Library.
3. Configurer Google Auth Platform / OAuth consent screen. Pour un compte Google personnel, utiliser External ; ajouter le compte Google dans Test users si nécessaire.
4. Dans Clients, créer un OAuth Client ID de type **Desktop app** et télécharger le nouveau client_secret.json sur votre PC privé. Ne jamais ajouter ce fichier à Git.
5. L'application demande uniquement le scope drive.appdata (données privées de cette application, et non l'ensemble du Drive).

**Attention :** une application OAuth « External / Testing » peut délivrer un refresh token qui expire après 7 jours. Pour un service durable, configurer le statut et l'approbation OAuth appropriés selon les règles Google.

### 2. Générer des valeurs RÉELLES sur votre propre ordinateur (Windows 11)

Le fichier `scripts/create_gdrive_token.py` génère désormais **les trois secrets Streamlit** : le nouveau jeton OAuth, l'adresse e-mail choisie par le titulaire et un mot de passe initial aléatoire fort. Aucune valeur secrète n'est envoyée sur GitHub ou dans le chat.

Ouvrir PowerShell (dans le dossier voulu) :

~~~powershell
git clone https://github.com/ridouanalhannachi/reclamation_estn.git
cd reclamation_estn
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/create_gdrive_token.py --client-secret "$env:USERPROFILE\Downloads\client_secret.json"
~~~

Si le dépôt est déjà téléchargé, remplacer les deux premières commandes par `git pull origin main`. Adapter le nom réel du fichier OAuth téléchargé si différent (par exemple `client_secret_123.apps.googleusercontent.com.json`). Le script demandera **ton adresse e-mail administrateur**, ouvrira Google dans le navigateur, obtiendra le consentement pour drive.appdata et générera un mot de passe temporaire aléatoire.

À la fin, le fichier privé suivant est créé **sur ton PC**, hors du dépôt :

~~~text
%USERPROFILE%\reclamation-estn-private\streamlit-secrets.toml
~~~

Il contient **trois lignes avec les valeurs authentiques**. Les copier intégralement dans Streamlit Cloud. Le script n'affiche pas le mot de passe ni le jeton dans le terminal, et refuse d'écraser un fichier déjà présent.

### 3. Configurer Streamlit Community Cloud

1. Ouvrir https://share.streamlit.io/ et choisir l'application Réclamations ESTN.
2. Aller dans `⋯ → Settings → Secrets` et supprimer les anciens secrets de connexion PostgreSQL / l'ancien jeton exposé.
3. Coller **exactement le contenu du fichier privé généré** : les clés réelles sont GDRIVE_TOKEN_B64, BOOTSTRAP_ADMIN_EMAIL et BOOTSTRAP_ADMIN_PASSWORD, à la racine TOML.
4. Cliquer **Save**, puis **Reboot app**.
5. Se connecter avec l'adresse e-mail indiquée et le mot de passe initial du fichier local ; le changer immédiatement à la première connexion.

**Paramètres de déploiement réels :**
- Repository : `ridouanalhannachi/reclamation_estn`
- Branch : `main`
- Main file path : `streamlit_app.py`

Aucun DATABASE_URL, APP_MODE ou APP_PASSWORD n'est nécessaire en mode Google Drive ; un ancien DATABASE_URL est ignoré quand GDRIVE_TOKEN_B64 est renseigné. Le nom de sous-domaine de Streamlit est à choisir ou à vérifier dans les paramètres de ton application.

Le premier administrateur est créé dans le nouvel espace Google Drive et doit changer son mot de passe à sa première connexion. Les étudiants sont ajoutés par import CSV, chaque compte possédant son propre mot de passe haché.

### Limites et confidentialité

- Ne pas déposer le fichier de jeton, les secrets Google OAuth ou les identifiants étudiants dans GitHub, les captures d'écran ou les discussions.
- appDataFolder est caché dans l'interface Google Drive ordinaire. Il ne peut pas être partagé directement ; ce n'est pas un dossier que vous pourrez parcourir manuellement sur drive.google.com.
- Le stockage repose sur des documents append-only. Google Drive n'est **pas** une base de données transactionnelle : les accès simultanés, les quotas, les conflits et les délais nécessitent des tests avant un usage institutionnel.
- La V1.2 ne migre pas automatiquement les données de SQLite, PostgreSQL ni de l'ancienne application. Aucun import n'efface les données précédentes.
- L'export complet, la restauration testée, le journal de sécurité et la limitation des tentatives de connexion restent à développer.
- Pour les données réelles d'étudiants, faire approuver la sécurité, la durée de conservation, les sauvegardes, les accès et les exigences légales marocaines avant mise en ligne.
