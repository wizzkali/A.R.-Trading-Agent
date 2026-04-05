#!/bin/bash
# setup_github.sh - Ejecutar después de crear los archivos localmente

echo "Inicializando repositorio Git..."
git init
git add .
git commit -m "Initial commit: Rothstein v6.0"

echo "Creando repositorio remoto en GitHub (requiere token)..."
gh repo create sindicato/rothstein --private --source=. --remote=origin --push

echo "Repositorio subido. Configurando protección de rama..."
gh api -X PUT repos/sindicato/rothstein/branches/main/protection \
  --field required_status_checks='{}' \
  --field enforce_admins=true \
  --field required_pull_request_reviews='{"required_approving_review_count":1}'

echo "Listo. Clona en otro lado con: git clone https://github.com/sindicato/rothstein.git"
