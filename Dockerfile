FROM python:3.11-slim

# Логи сразу в docker logs, без .pyc в контейнере
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Отдельный пользователь без прав администратора.
RUN groupadd --gid 1000 bot \
    && useradd --uid 1000 --gid bot --no-create-home --shell /usr/sbin/nologin bot

# Зависимости отдельным слоем: при правке кода не переустанавливаются
COPY requirements.txt .
RUN pip install -r requirements.txt

# Код (секреты исключены через .dockerignore)
COPY --chown=bot:bot . .

USER bot

CMD ["python", "app.py"]
