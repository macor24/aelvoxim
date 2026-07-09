FROM python:3.12-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install aelvoxim
RUN pip install --no-cache-dir -e .

# Runtime
EXPOSE 9701
ENV AELVOXIM_EDITION=community
ENV PYTHONPATH=/app/src

CMD ["aelvoxim", "server", "--port", "9701"]
