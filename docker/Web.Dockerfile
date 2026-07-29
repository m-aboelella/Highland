FROM node:22-alpine AS dependencies

WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM dependencies AS build

ARG NEXT_PUBLIC_HIGHLAND_API_URL=http://127.0.0.1:8080
ENV NEXT_PUBLIC_HIGHLAND_API_URL=${NEXT_PUBLIC_HIGHLAND_API_URL}
COPY web ./
RUN npm run build

FROM node:22-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/.next ./.next
EXPOSE 3000
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0"]
