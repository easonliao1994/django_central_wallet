# Django 4.1 supports Python up to 3.11.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# settings.py is git-ignored; the image uses the committed template, configured by environment variables.
RUN cp django_central_wallet/settings.py.default django_central_wallet/settings.py \
    && SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["gunicorn", "django_central_wallet.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
