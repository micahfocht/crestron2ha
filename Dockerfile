FROM --platform=$BUILDPLATFORM python:3.13.3-slim

LABEL Maintainer="micahfocht"

ENV SWITCHER_IP=''
ENV MQTT_BROKER=''

# Update OS packages to patch vulnerabilities and clean up
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies without cache
RUN pip install --no-cache-dir paho-mqtt exscript

# Create non-root user and app directory
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

WORKDIR /app

# Copy application files
COPY main.py .
COPY crestron_parser.py .

# Switch to non-root user
USER appuser

CMD ["python3", "-u", "main.py"]