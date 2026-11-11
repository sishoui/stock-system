FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt fastapi 'uvicorn[standard]'
COPY app ./app
COPY pytest.ini ./
ENV STOCK_DB_URL=sqlite:////data/stock.db
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
