# Utiliser une image Python officielle
FROM python:3.11-slim

# Installer les dépendances système pour Playwright
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Définir le répertoire de travail
WORKDIR /app

# Copier les fichiers de requirements
COPY requirements.txt .

# Installer les dépendances Python
RUN pip install --no-cache-dir -r requirements.txt

# Installer Playwright et Chromium
RUN playwright install chromium
RUN playwright install-deps chromium

# Copier le code de l'application
COPY . .

# Exposer le port
EXPOSE $PORT

# Commande pour démarrer l'application
CMD gunicorn --bind 0.0.0.0:$PORT app:app