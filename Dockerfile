FROM python:3.11-slim as builder

WORKDIR /app

# Install only build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Copy wheels from builder stage
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .

# Install packages from wheels
RUN pip install --no-cache /wheels/*

# Copy source code
COPY src/ ./src/

# Set the entrypoint
ENTRYPOINT ["python", "-m", "src.main"]
