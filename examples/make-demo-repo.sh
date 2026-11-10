#!/usr/bin/env bash
# Build a small throwaway git repository with a `main` branch and a `feature`
# branch, so you can try change-risk-scorer without pointing it at real code.
#
#   bash examples/make-demo-repo.sh /tmp/risk-demo
set -euo pipefail

dir="${1:?usage: make-demo-repo.sh <new-directory>}"
if [ -e "$dir" ]; then
  echo "error: $dir already exists; pick a new path" >&2
  exit 1
fi

mkdir -p "$dir"
cd "$dir"
git init -q -b main
git config user.name "Demo User"
git config user.email "demo@example.com"

mkdir -p services/payments services/orders db/migrations deploy/k8s tests
printf 'def charge(amount_cents, card_token):\n    return {"status": "ok", "amount": amount_cents}\n' > services/payments/charge.py
printf 'def create_order(items):\n    return {"items": items, "state": "new"}\n' > services/orders/api.py
printf 'CREATE TABLE orders (id BIGINT PRIMARY KEY, state TEXT NOT NULL);\n' > db/migrations/001_create_orders.sql
printf 'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\nspec:\n  replicas: 3\n' > deploy/k8s/orders.yaml
printf 'requests==2.32.3\n' > requirements.txt
printf 'from services.orders.api import create_order\n\ndef test_create_order():\n    assert create_order([])["state"] == "new"\n' > tests/test_orders.py
git add -A
git commit -q -m "Initial services"

git checkout -q -b feature
printf 'ALTER TABLE orders ADD COLUMN currency CHAR(3) NOT NULL DEFAULT '"'"'USD'"'"';\n' > db/migrations/002_add_currency.sql
printf 'def charge(amount_cents, card_token, currency="USD"):\n    if amount_cents <= 0:\n        raise ValueError("amount must be positive")\n    return {"status": "ok", "amount": amount_cents, "currency": currency}\n' > services/payments/charge.py
printf 'def create_order(items, currency="USD"):\n    return {"items": items, "state": "new", "currency": currency}\n' > services/orders/api.py
printf 'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: orders\nspec:\n  replicas: 6\n' > deploy/k8s/orders.yaml
printf 'requests==2.32.3\nstripe==11.1.0\n' > requirements.txt
git add -A
git commit -q -m "Add multi-currency support"

echo "Demo repository created at $dir (branches: main, feature)"
