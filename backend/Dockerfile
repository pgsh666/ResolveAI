FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The editable resolver input is followed by the audited, exact deployment lock.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && pip check
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
