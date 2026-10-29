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
