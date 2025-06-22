# Tennis à Paris Dashboard

Un **tableau de bord interactif** montrant les équipements de Tennis à Paris, avec un **backend FastAPI** déployé sur AWS Lambda + API Gateway, et un **frontend statique** hébergé sur Amazon S3.

---

## 🏗️ Architecture globale

```text
Browser        ↔️  CloudFront/S3 (frontend statique)
  │                                 │
  │        fetch API_BASE/...       │
  ▼                                 ▼
Index.html  ———► API Gateway ——— AWS Lambda (FastAPI)
                   │               │
                   │               ├── Athena (requêtes SQL)
                   │               └── S3 (données & stockage feedback)
                   ▼
                Réponse JSON
```

---

## 🚀 Fonctionnalités

1. **Données tennis Paris** via Athena/Glue :

   * `/api/tennis_paris` : liste brute des terrains (adresse, coordonnées, nature, propriété…)
   * `/api/tennis_paris/metrics` : agrégations par arrondissement (totaux, dates, surfaces, accès transports & handicap, série temporelle)
2. **Feedback utilisateur** :

   * `/api/feedback` (POST) enregistre l’avis (accepted: bool) dans un bucket S3
3. **Sauvegarde de requêtes** :

   * `/save` (POST, Form) exécute une requête Athena et écrit le CSV dans S3 avec lien présigné
4. **Frontend statique** :

   * Graphiques Chart.js (barres, camembert, bubble, ligne)
   * Carte interactive Leaflet avec filtres par type et propriété
   * Interface responsive & design « bleu-blanc-rouge »

---

## 🔧 Test local — Glue Catalog Explorer

Une application **FastAPI** avec une interface **Jinja2** pour explorer votre **Glue Data Catalog**, lister les bases de données, les tables et exécuter des requêtes **SELECT** via **Athena**, le tout dans une charte graphique « bleu-blanc-rouge ».

---

### 📋 Prérequis

* **Python 3.8+**
* **AWS CLI** configuré (`aws configure`) ou variables d’environnement :

  * `AWS_ACCESS_KEY_ID`
  * `AWS_SECRET_ACCESS_KEY`
  * `AWS_REGION` (ex : `eu-west-3`)
* **Glue Data Catalog** avec au moins une base et des tables
* **Bucket S3** pour les résultats Athena (ex : `s3://mon-bucket-athena/results/`)

---

### 🚀 Installation

1. **Cloner le dépôt** :

   ```bash
   git clone <votre-repo-url>
   cd api
   ```
2. **Installer les dépendances** :

   ```bash
   pip install fastapi uvicorn boto3 jinja2 python-multipart mangum
   ```
3. **Configurer** la variable `ATHENA_OUTPUT` dans `main.py` : l’URL de votre bucket S3 (dossier `results/`).
4. **Vérifier** que votre région dans `main.py` (`region_name`) correspond à votre configuration AWS.

---

### 🗂️ Structure du projet

```
api/
├── main.py           # Code principal FastAPI + Jinja2
├── requirements.txt  # (optionnel) liste des paquets Python
├── static/
│   └── style.css     # Feuille de style bleu-blanc-rouge
└── templates/
    └── index.html    # Template Jinja2 de l’interface utilisateur
```

---

### ▶️ Lancer l’application

```bash
uvicorn main:app --reload --port 8000
```

* **Swagger UI** : [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **Interface utilisateur** : [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

### 🖥️ Fonctionnalités

1. **Page d’accueil** : liste des bases Glue disponibles.
2. **Liste des tables** : après sélection d’une base, affichage des tables.
3. **Exécution de requêtes** : form permettant d’écrire un `SELECT` personnalisé (avec `LIMIT`) et affichage des résultats dans un tableau.
4. **Charte graphique tricolore** : bleu-blanc-rouge pour les en-têtes, boutons et styles.

---

## 🚀 Déploiement AWS via SAM

1. **Configurer** votre profil AWS CLI (e.g. `aws configure --profile tennis`)
2. **Builder** l’application Lambda :

   ```bash
   cd api
   sam build --use-container
   ```
3. **Déployer** (une fois, guide interactif) :

   ```bash
   sam deploy --guided
   ```
4. **Note** : récupérer la valeur `API_BASE` renvoyée (ex : `https://xxxx.execute-api.eu-west-3.amazonaws.com/Prod`)

---

## 📦 Déploiement du Frontend sur S3

1. **Créer** un bucket S3 statique (site web) en `eu-west-3`
2. **Rendre public** en lecture et activer l’hébergement statique
3. **Synchroniser** :

   ```bash
   aws s3 sync front/ s3://<mon-bucket>/ --acl public-read
   ```
4. **Mettre à jour** `API_BASE` dans `front/index.html` et `front/map.html`

---

## 🤖 CI/CD avec GitHub Actions

Le pipeline définit dans `.github/workflows/ci-cd.yml` :

1. Checkout du code
2. Build & déploiement de l’API SAM
3. Synchro du dossier `front/` sur S3

**Variables** :

* `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`, `API_STACK_NAME`

---

## 🤝 Contribuer

Les contributions sont les bienvenues ! Ouvrez une issue ou un pull request pour proposer des améliorations, corrections ou nouvelles fonctionnalités.

---

© 2025 • Neila, Ferdaous & Natali • Tableau de bord Tennis à Paris
