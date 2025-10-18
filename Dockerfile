FROM python:3.11-slim

# Install build deps (adjust if you need system libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install early so layer caching works
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

ENV PORT=8000
EXPOSE 8000

# Use gunicorn as the production WSGI server
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "run:app"]
