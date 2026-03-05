FROM python:3.12-slim

# Install system dependencies (FFmpeg for audio, others for PyNaCl encryption)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
COPY smucko_music.py .

# Install Python libraries
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "smucko_music.py"]