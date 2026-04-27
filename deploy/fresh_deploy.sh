#!/bin/bash
set -euo pipefail

REPO_DIR="/home/deploy/Roobico"
BRANCH="develop"
REPO_URL="git@github.com:dimboychuk1995/Roobico.git"
TMP_ENV="/tmp/roobico.env.$(date +%s)"

echo "==> Stopping roobico.service"
systemctl stop roobico.service || true

echo "==> Backing up .env to ${TMP_ENV}"
cp -a "${REPO_DIR}/.env" "${TMP_ENV}"

echo "==> Wiping ${REPO_DIR} contents"
sudo -u deploy bash -c "
  shopt -s dotglob
  rm -rf ${REPO_DIR}/*
"

echo "==> Cloning ${BRANCH} fresh"
sudo -u deploy bash -c "
  cd /home/deploy
  rmdir Roobico
  GIT_SSH_COMMAND='ssh -i /home/deploy/.ssh/id_ed25519 -o StrictHostKeyChecking=no' \
    git clone --branch ${BRANCH} --single-branch ${REPO_URL} Roobico
  cd Roobico
  git checkout -B production
  git log --oneline -1
"

echo "==> Restoring .env"
cp -a "${TMP_ENV}" "${REPO_DIR}/.env"
chown deploy:deploy "${REPO_DIR}/.env"
chmod 644 "${REPO_DIR}/.env"

echo "==> Creating venv + installing requirements"
sudo -u deploy bash -c "
  cd ${REPO_DIR}
  python3 -m venv venv
  ./venv/bin/pip install --upgrade pip
  ./venv/bin/pip install -r requirements.txt
"

echo "==> Starting roobico.service"
systemctl start roobico.service
sleep 3
systemctl is-active roobico.service
journalctl -u roobico.service -n 15 --no-pager
echo "==> Done. .env backup at ${TMP_ENV}"
