python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -U -r requirements.txt
cp django_central_wallet/settings.py.default django_central_wallet/settings.py
cp db.sqlite3.default db.sqlite3
# python3 manage.py collectstatic
python3 manage.py migrate
python3 manage.py runserver 0:8000