FROM node:22-alpine AS frontend
WORKDIR /app
COPY package*.json ./
COPY index.html ./
COPY vite.config.ts tsconfig*.json postcss.config.js tailwind.config.js components.json ./
COPY src ./src
RUN npm ci
RUN npm run build

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend ./backend
COPY --from=frontend /app/dist ./dist
ENV PORT=8080
EXPOSE 8080
CMD ["python", "backend/main.py"]
