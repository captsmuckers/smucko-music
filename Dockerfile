FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# --- ADD THIS LINE ---
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
COPY smucko_music.py .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "smucko_music.py"]