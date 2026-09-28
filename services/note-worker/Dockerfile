# syntax=docker/dockerfile:1
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY pyproject.toml ./
COPY src src
RUN --mount=type=cache,target=/root/.cache/pip pip install .
USER 10001
EXPOSE 9100
ENTRYPOINT ["python", "-m", "note_worker"]
