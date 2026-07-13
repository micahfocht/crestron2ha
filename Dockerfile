# On an ARM development machine, build the AMD64 deployment image with:
# docker build --platform linux/amd64 -t crestron2ha:latest .
FROM python:3.13.3-slim

LABEL Maintainer="micahfocht"

# Environment variable for initial config generation (optional)
# Set CRESTRON_HOST to auto-generate config on first run
ENV CRESTRON_HOST=''
ENV CRESTRON_PORT=41795

# Update OS packages to patch vulnerabilities and clean up
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install pinned Python dependencies without cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user and app directory
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app /config && \
    chown -R appuser:appuser /app /config

WORKDIR /app

# Copy application files
COPY main.py .
COPY crestron_parser.py .
COPY config_loader.py .
COPY tesira_controller.py .

# Switch to non-root user
USER appuser

# Config volume for persistent configuration
VOLUME ["/config"]

CMD ["python3", "-u", "main.py"]
