FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN useradd -r -u 10001 optdesk
USER optdesk
EXPOSE 8000
# Role is the container command: feed | pricer | gateway
ENTRYPOINT ["python", "-m", "optdesk"]
CMD ["gateway"]
