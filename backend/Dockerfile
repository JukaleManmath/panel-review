FROM python:3.12-slim-bookworm

# WeasyPrint system dependencies (correction #12)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying code — preserves layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["sh", "-c", "python manage.py collectstatic --noinput && python manage.py migrate --noinput && daphne -b 0.0.0.0 -p ${PORT:-8080} config.asgi:application"]
