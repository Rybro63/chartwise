# syntax=docker/dockerfile:1
FROM node:26-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29-alpine
# The nginx entrypoint renders templates with envsubst, replacing only variables that are set,
# so nginx's own $host/$uri survive.
ENV ENCOUNTER_SERVICE_URL=http://encounter-service:8080
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 8080
