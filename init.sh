python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -U -r requirements.txt
cp .env.default .env
# Edit .env: set SECRET_KEY and WALLET_ENCRYPTION_KEYS before migrating
cp django_central_wallet/settings.py.default django_central_wallet/settings.py
cp db.sqlite3.default db.sqlite3
# python3 manage.py collectstatic
python3 manage.py migrate
# Background jobs (need Redis), each in its own terminal:
#   celery -A django_central_wallet worker -l info
#   celery -A django_central_wallet beat -l info
python3 manage.py runserver 0:8000