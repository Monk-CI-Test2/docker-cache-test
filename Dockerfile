# Minimal build-pipeline smoke test — builds in seconds.
# Two stages sharing one base image, so you exercise multi-stage
# builds + layer caching without any heavy compilation.

# ---- Stage 1: builder — produces a tiny artifact ----
FROM alpine:3.20 AS builder
WORKDIR /app
# one lightweight package layer, so there's something real to cache
RUN apk add --no-cache coreutils
RUN echo "build stage ran at $(date -u)" > /app/build-output.txt

# ---- Stage 2: runner — minimal final image ----
FROM alpine:3.20 AS runner
WORKDIR /app
COPY --from=builder /app/build-output.txt ./
CMD ["cat", "build-output.txt"]
