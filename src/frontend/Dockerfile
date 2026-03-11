FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json /app/
RUN npm ci

COPY . /app
RUN npm run build

FROM nginx:1.27-alpine

ENV API_UPSTREAM=http://backend:8000

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
