> **Rendu / Submission** ce dépôt contient la solution de l'examen.
> Les choix d'implémentation, la justification des métriques personnalisées
> et l'alerte testée par `make fire-alert` sont documentés dans
> **[SOLUTION.md](SOLUTION.md)**. Démarrage : `make`.
>
> Implementation choices, the justification of the custom metrics and the
> alert exercised by `make fire-alert` are documented in **SOLUTION.md**.
> To start everything: `make`.

---

# Examen du cours Prometheus & Grafana. (English version below)

### Structure du repo :

```
├── deployment
│   ├── prometheus/
│   │   └── prometheus.yml
├── src/
│   ├── api/
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── evaluation/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── run_evaluation.py
├── docker-compose.yml
└── Makefile
```

#### Contexte de l'Examen

Vous êtes chargé(e) de mettre en place une solution de monitoring complète pour un modèle de prédiction du nombre de vélos partagés (`cnt`) basé sur le dataset "Bike Sharing UCI". L'objectif est de garantir que les performances du modèle et les dérives des données sont constamment surveillées, visualisées et alertables, avec un accent particulier sur l'automatisation de la création des dashboards Grafana.

Vous partirez du dépôt Git suivant :

```
https://github.com/DataScientest/PromGraf-MLOps-Exam-Student
```

Celui-ci qui contient une structure de base vide pour l'API et les configurations.

**Variables clés du dataset :**
*   **Variable cible (`target`) :** `cnt`
*   **Variables numériques :** `temp`, `atemp`, `hum`, `windspeed`, `mnth`, `hr`, `weekday`
*   **Variables catégorielles :** `season`, `holiday`, `workingday`, `weathersit`

#### Consignes Générales

1.  **Qualité du Code et de la Configuration :** Le code doit être propre, commenté et les configurations claires et bien structurées.
2.  **Reproductibilité :** Le projet doit pouvoir être lancé et fonctionnel sur une autre machine en exécutant une simple commande `make`.
3.  **Automatisation :** Privilégiez l'automatisation chaque fois que possible, notamment pour la mise en place des dashboards Grafana.
4.  **Versionnement :** Assurez-vous que tous les fichiers nécessaires (code, configurations, dashboards JSON) sont inclus dans votre rendu.

#### Tâches Spécifiques

Pour réussir cet examen, vous devrez implémenter les points suivants :

**I. Préparation de l'Environnement et de l'API :**

*   **Construction de l'API :**
    *   Vous devrez **construire l'API FastAPI** (`src/api/main.py`) pour un modèle de régression prédisant le nombre de vélos (`cnt`).
    *   **Intégrez les fonctions de chargement et de préparation des données** (`_fetch_data`, `_process_data`) ainsi que **d'entraînement du `RandomForestRegressor`** (`_train_and_predict_reference_model`) sur les données de janvier 2011. Le modèle doit être entraîné une seule fois (par exemple, au démarrage du conteneur API ou via une cible `make train` dédiée) et chargé pour l'inférence.
    *   Votre API devra exposer un endpoint `/predict` qui accepte les features du `Bike Sharing` dataset (la class `BikeSharingInput` fournie) et retourne une prédiction.
    *   Assurez-vous que le `Dockerfile` et le `requirements.txt` de votre API sont corrects (incluant toutes les dépendances nécessaires).
*   Configurez le `docker-compose.yml` pour lancer :
    *   Votre API (que vous nommerez `bike-api`, sur le port 8080).
    *   Prometheus (sur le port 9090).
    *   Grafana (sur le port 3000).
    *   `node-exporter` (sur le port 9100) pour le monitoring de l'infrastructure hôte.

**II. Instrumentation de l'API et Collecte de Métriques dans Prometheus :**

*   Dans le fichier `api/main.py` :
    *   Définissez et incrémentez les métriques suivantes (utilisant `prometheus_client` et votre `CollectorRegistry`) :
        *   `api_requests_total` (Counter, avec labels `endpoint`, `method`, `status_code`).
        *   `api_request_duration_seconds` (Histogram, avec labels `endpoint`, `method`, `status_code`).
        *   `model_rmse_score` (Gauge, pour le Root Mean Squared Error du modèle de régression, mise à jour via l'endpoint `/evaluate`).
        *   `model_mae_score` (Gauge, pour le Root Mean Squared Error du modèle de régression, mise à jour via l'endpoint `/evaluate`).
        *   `model_r2_score` (Gauge, pour le Root Mean Squared Error du modèle de régression, mise à jour via l'endpoint `/evaluate`).
        *   **Une métrique de votre choix :** Implémentez **une métrique supplémentaire jugée pertinente** pour le monitoring de ce modèle de régression et des dérives (par exemple, `model_mape_score`, `evidently_data_drift_detected_status` (Gauge), ou le score de dérive pour une feature spécifique). Justifiez brièvement (dans les commentaires du code ou un `README` rapide) pourquoi cette métrique est pertinente.
    *   Implémentez l'endpoint `/evaluate` :
        *   Il acceptera des données "courantes" pour une période (ex: une semaine de février).
        *   Il utilisera le modèle entraîné pour faire des prédictions sur ces données.
        *   Il exécutera un **rapport Evidently** (`RegressionPreset` ou `DataDriftPreset`) en utilisant les données de janvier (référence) et les données "courantes" fournies.
        *   **Il extraira le `RMSE`, `MAE`, `R2Score` et votre métrique de choix** des résultats du rapport Evidently (utilisez la documentation d'Evidently ou la structure des objets `Report` / `Snapshot` / `Metric` pour cela) et mettra à jour les `Gauge` ou `Counter` Prometheus correspondants.
        *   la class `EvaluationReportOutput` vous donne un exemple de format de sortie attendu.
    *   Exposez toutes ces métriques via l'endpoint `/metrics`.
*   Configurez `deployment/prometheus/prometheus.yml` pour :
    *   Scraper votre API `bike-api`.
    *   Scraper `node-exporter`.
*   Créez un fichier `deployment/prometheus/rules/alert_rules.yml` et configurez au moins une règle d'alerte Prometheus (par exemple, si l'API est `down`).

**III. Visualisation Automatisée avec Grafana :**

*   Configurez Grafana pour qu'il soit lancé avec Docker Compose.
*   **Implémentez le "Dashboards as Code" :**
    *   Créez un dossier `deployment/grafana/dashboards/` et placez-y des fichiers JSON de dashboards préalablement crées (par vous même via l'interface, récupérés sur le Hub Grafana, etc.).
    *   Configurez le provisioning de Grafana via un fichier YAML (par exemple, `deployment/grafana/provisioning/dashboards.yaml`) pour qu'il charge automatiquement ces dashboards au démarrage.
*   **Créez trois dashboards distinct :**
    *   **Dashboard "API Performance" :** Doit inclure des panels pour le taux de requêtes, la latence (P95), et le taux d'erreur de votre API.
    *   **Dashboard "Model Performance & Drift" :** Doit inclure des panels pour les scores du modèle (`model_rmse_score`, `model_mae_score`, `model_r2_score`, etc.) et la métrique personnalisée que vous avez choisie (ex: score de dérive de données).
    *   **Dashboard "Infrastructure Overview" :** Doit inclure des panels pour l'utilisation CPU, RAM, et l'espace disque (via `node-exporter`).

    > Vous êtes libres d'ajouter des panels pour chacun de dashboards, si les metrics qu'ils suivent sont pertinentes pour le dashboard en question.

**IV. Alerting (Prometheus et Grafana) :**

*   Vous devez avoir une alerte configurée dans Prometheus, comme demandé plus haut.
*   **Configurez au moins une alerte directement dans l'interface de Grafana.** Cette alerte doit être basée sur une métrique ML (ex: si le RMSE du modèle dépasse un seuil, ou si votre métrique de dérive choisie indique un problème).

**V. Simulation de Trafic et Évaluation :**

*   Le script Python `run_evaluation.py` vous sera **fourni**. Ce script permettra de charger un echantillon de données du dataset "Bike Sharing" pour des périodes spécifiques (ex: semaines de février) et d'envoyer ces données à l'endpoint `/evaluate` de votre API. Vous devrez vous assurer que votre endpoint `/evaluate` accepte le format de données envoyé par ce script.
*   Fournissez un script simple (ou une commande `curl` répétée dans un script shell) pour générer du trafic sur l'endpoint `/predict` afin de simuler l'utilisation réelle de l'API.

**VI. Livrables :**

*   Rendez une **archive `.tar` ou `.zip`** de l'ensemble de votre projet.
*   L'archive doit contenir tous les fichiers nécessaires (votre `docker-compose.yml`, votre dossier `src`, votre dossier `deployment`, votre `makefile`, etc.).
*   Le projet doit contenir un **`Makefile` à la racine** avec les cibles suivantes :
    *   `all` : Pour démarrer tous les services (API, Prometheus, Grafana, Node Exporter).
    *   `stop` : Pour arrêter tous les services.
    *   `evaluation` : Pour exécuter le script `run_evaluation.py`, qui mettra à jour les métriques dans Prometheus.
    *   `fire-alert` : Pour déclencher intentionnellement une des alertes que vous avez configurées (ex: un endpoint `/trigger-drift` dans votre API qui simule une dérive en retournant des valeurs extrêmes à Evidently, ou en forçant le RMSE à être très élevé pour un petit lot de données). Vous devrez justifier quelle alerte est testée.

---

# Exam of the Prometheus & Grafana course.

### Repository Structure :

```
├── deployment
│   ├── prometheus/
│   │   └── prometheus.yml
├── src/
│   ├── api/
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── evaluation/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── run_evaluation.py
├── docker-compose.yml
└── Makefile
```

#### Exam Context

You are tasked with implementing a comprehensive monitoring solution for a bike sharing prediction model (`cnt`) based on the "Bike Sharing UCI" dataset. The goal is to ensure that the model's performance and data drifts are constantly monitored, visualized, and alertable, with a particular focus on automating the creation of Grafana dashboards.

You will start from the following Git repository:

https://github.com/DataScientest/PromGraf-MLOps-Exam-Student

This one contains an empty base structure for the API and configurations.

**Key variables of the dataset:**
*   **Target variable (`target`):** `cnt`
*   **Numerical variables:** `temp`, `atemp`, `hum`, `windspeed`, `mnth`, `hr`, `weekday`
*   **Categorical variables:** `season`, `holiday`, `workingday`, `weathersit`

#### General Instructions

1.  **Code and Configuration Quality:** The code must be clean, commented, and the configurations clear and well-structured.
2.  **Reproducibility:** The project must be able to run and function on another machine by executing a simple command `make`.
3.  **Automation:** Favor automation whenever possible, especially for setting up Grafana dashboards.
4.  **Versioning:** Ensure that all necessary files (code, configurations, JSON dashboards) are included in your submission.

#### Specific Tasks

To succeed in this exam, you will need to implement the following points:

**I. Preparation of the Environment and the API:**

*   **API Construction:**
    *   You will need to **build the FastAPI** (`src/api/main.py`) for a regression model predicting the number of bikes (`cnt`).
    *   **Integrate the data loading and preparation functions** (`_fetch_data`, `_process_data`) as well as **training the `RandomForestRegressor`** (`_train_and_predict_reference_model`) on the data from January 2011. The model should be trained only once (for example, at the startup of the API container or via a dedicated `make train` target) and loaded for inference.
    *   Your API should expose an endpoint `/predict` that accepts the features from the `Bike Sharing` dataset (the `BikeSharingInput` class provided) and returns a prediction.
    *   Ensure that the `Dockerfile` and `requirements.txt` of your API are correct (including all necessary dependencies).
*   Configure the `docker-compose.yml` to launch:
    *   Your API (which you will name `bike-api`, on port 8080).
    *   Prometheus (on port 9090).
    *   Grafana (on port 3000).
    *   `node-exporter` (on port 9100) for monitoring the host infrastructure.

**II. API Instrumentation and Metrics Collection in Prometheus:**

*   In the file `api/main.py`:
    *   Define and increment the following metrics (using `prometheus_client` and your `CollectorRegistry`):
        *   `api_requests_total` (Counter, with labels `endpoint`, `method`, `status_code`).
        *   `api_request_duration_seconds` (Histogram, with labels `endpoint`, `method`, `status_code`).
        *   `model_rmse_score` (Gauge, for the Root Mean Squared Error of the regression model, updated via the `/evaluate` endpoint).
        *   `model_mae_score` (Gauge, for the Mean Absolute Error of the regression model, updated via the `/evaluate` endpoint).
        *   `model_r2_score` (Gauge, for the R-squared score of the regression model, updated via the `/evaluate` endpoint).
        *   **A metric of your choice:** Implement **an additional metric deemed relevant** for monitoring this regression model and drifts (for example, `model_mape_score`, `evidently_data_drift_detected_status` (Gauge), or the drift score for a specific feature). Briefly justify (in code comments or a quick `README`) why this metric is relevant.
    *   Implement the `/evaluate` endpoint:
        *   It will accept "current" data for a period (e.g., one week in February).
        *   It will use the trained model to make predictions on this data.
        *   It will execute an **Evidently report** (`RegressionPreset` or `DataDriftPreset`) using the January data (reference) and the provided "current" data.
        *   **It will extract the `RMSE`, `MAE`, `R2Score`, and your chosen metric** from the Evidently report results (use the Evidently documentation or the structure of `Report` / `Snapshot` / `Metric` objects for this) and update the corresponding Prometheus `Gauge` or `Counter`.
        *   The class `EvaluationReportOutput` gives you an example of the expected output format.
    *   Expose all these metrics via the `/metrics` endpoint.
*   Configure `deployment/prometheus/prometheus.yml` to:
    *   Scrape your API `bike-api`.
    *   Scrape `node-exporter`.
*   Create a file `deployment/prometheus/rules/alert_rules.yml` and configure at least one Prometheus alert rule (for example, if the API is `down`).

**III. Automated Visualization with Grafana:**

*   Configure Grafana to run with Docker Compose.
*   **Implement "Dashboards as Code":**
    *   Create a folder `deployment/grafana/dashboards/` and place JSON files of dashboards previously created (by yourself via the interface, retrieved from the Grafana Hub, etc.) in it.
    *   Configure Grafana provisioning via a YAML file (for example, `deployment/grafana/provisioning/dashboards.yaml`) to automatically load these dashboards on startup.
*   **Create three distinct dashboards:**
    *   **Dashboard "API Performance":** Must include panels for request rate, latency (P95), and error rate of your API.
    *   **Dashboard "Model Performance & Drift":** Must include panels for model scores (`model_rmse_score`, `model_mae_score`, `model_r2_score`, etc.) and the custom metric you have chosen (e.g., data drift score).
    *   **Dashboard "Infrastructure Overview":** Must include panels for CPU usage, RAM, and disk space (via `node-exporter`).

> You are free to add panels for each dashboard if the metrics they track are relevant to the dashboard in question.

**IV. Alerting (Prometheus and Grafana):**

*   You must have an alert configured in Prometheus, as requested above.
*   **Configure at least one alert directly in the Grafana interface.** This alert should be based on an ML metric (e.g., if the model's RMSE exceeds a threshold, or if your chosen drift metric indicates a problem).

**V. Traffic Simulation and Evaluation:**

*   The Python script `run_evaluation.py` will be **provided** to you. This script will load a sample of data from the "Bike Sharing" dataset for specific periods (e.g., weeks of February) and send this data to the `/evaluate` endpoint of your API. You will need to ensure that your `/evaluate` endpoint accepts the data format sent by this script.
*   Provide a simple script (or a repeated `curl` command in a shell script) to generate traffic on the `/predict` endpoint to simulate real usage of the API.

**VI. Deliverables :**

*   Create a **`.tar` or `.zip` archive** of your entire project.
*   The archive must contain all necessary files (your `docker-compose.yml`, your `src` folder, your `deployment` folder, your `makefile`, etc.).
*   The project must contain a **`Makefile` at the root** with the following targets:
    *   `all`: To start all services (API, Prometheus, Grafana, Node Exporter).
    *   `stop`: To stop all services.
    *   `evaluation`: To run the `run_evaluation.py` script, which will update the metrics in Prometheus.
    *   `fire-alert`: To intentionally trigger one of the alerts you have configured (e.g., an endpoint `/trigger-drift` in your API that simulates drift by returning extreme values to Evidently, or by forcing the RMSE to be very high for a small batch of data). You will need to justify which alert is being tested.
