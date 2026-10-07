FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The stylesheet is already compiled and committed, so no Node is needed here.
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data

USER app
EXPOSE 8000

# Migrations are applied on every start, so upgrading is: pull, rebuild, restart.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py ensure_admin && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-2} --access-logfile -"]
