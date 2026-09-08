# Dev-режим (vite dev server), не production-сборка -- на этапе хакатона
# удобнее видеть правки сразу, а не пересобирать образ на каждый чих.
# docker-compose.yml монтирует ./frontend поверх /app для hot-reload.
FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
