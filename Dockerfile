FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md main.py ./
COPY app ./app
COPY sql ./sql

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN mkdir -p /app/data

CMD ["python", "main.py", "--help"]
