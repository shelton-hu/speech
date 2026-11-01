FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OMP_NUM_THREADS=2
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends ffmpeg libsndfile1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --uid 10001 --create-home assistant
COPY models/ /models/
COPY app/ ./app/
COPY VERSION ./VERSION
ARG VERSION=0.2.0
ENV SPEECH_VERSION=$VERSION MODEL_DIR=/models
LABEL org.opencontainers.image.title="speech" org.opencontainers.image.version=$VERSION
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.gateway:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
