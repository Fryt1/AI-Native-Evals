ARG NODE_BASE_IMAGE=node:22-bookworm
FROM ${NODE_BASE_IMAGE}
WORKDIR /app
COPY gateway/server.mjs /app/server.mjs
USER node
EXPOSE 8080
CMD ["node", "/app/server.mjs"]
