# syntax=docker/dockerfile:1
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    WHISPER_MODEL=base.en WHISPER_MODEL_DIR=/models
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY pyproject.toml ./
COPY src src
RUN --mount=type=cache,target=/root/.cache/pip pip install .
# The model is baked into the image, so a running transcriber needs no internet access; the
# Kubernetes NetworkPolicy (deploy/k8s/base/transcriber.yaml) then allows it none.
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('base.en', download_root='/models')" \
    && chown -R app /models
ENV HF_HUB_OFFLINE=1
USER 10001
EXPOSE 9101
ENTRYPOINT ["python", "-m", "transcriber"]
