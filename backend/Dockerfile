FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for asyncpg + Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc ca-certificates \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 fonts-liberation \
    fonts-noto-color-emoji fonts-unifont \
    && rm -rf /var/lib/apt/lists/*

# Optional: extra CA certificates for users behind an SSL-inspecting
# corporate proxy. Accepts ``corporate-ca.crt`` (preferred name) or
# ``ca-certificates.crt`` (legacy). The COPY uses BuildKit wildcard
# semantics — it silently no-ops when no matching file exists, so a
# fresh clone with no cert builds without modification.
COPY corporate-ca*.crt* ca-certificates*.crt* /usr/local/share/ca-certificates/
RUN update-ca-certificates 2>/dev/null || true
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .

# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
