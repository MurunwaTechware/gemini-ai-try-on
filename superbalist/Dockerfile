# Products API (Superbalist catalogue), on port 8001. Normally started by compose.yaml with the
# "products" profile, next to the Cloud SQL Auth Proxy it reads the database through.
#
# Build on its own (from the project folder):
#   docker build -t tryon-products superbalist

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The modules import each other by name (import catalogue_db), so they all sit together in /app
COPY *.py ./

RUN useradd --uid 1000 --create-home app
USER app

EXPOSE 8001
CMD ["uvicorn", "products_api:app", "--host", "0.0.0.0", "--port", "8001"]
