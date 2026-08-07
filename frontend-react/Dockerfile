# Runs the Vite dev server, not a production build — this is the
# actively-iterated-on new frontend, and the dev server gives instant
# feedback on changes without a rebuild cycle. Before Azure deployment,
# switch this to a multi-stage build (`npm run build` -> serve dist/ with
# nginx or a static file server) for real production hardening.
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm install

COPY . .

EXPOSE 5174

CMD ["npm", "run", "dev"]
