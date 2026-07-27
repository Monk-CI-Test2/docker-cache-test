# Fault-matrix test image. Every cache assertion in the fault-matrix workflow
# is phrased as "is the step for salt X served CACHED by this builder?".
#
#   * the shared-base step is identical in every build -> always cacheable,
#   * the salt step keys ONLY on SALT (declared before the bloat ARGs, so bloat
#     values cannot invalidate it),
#   * the bloat step keys on SALT+BLOAT_SALT+BLOAT_MB and writes BLOAT_MB MiB of
#     urandom into the layer -> used to inflate the cache for LRU pressure.
FROM alpine:3.20
RUN echo shared-base > /base.txt
ARG SALT=none
RUN echo "salt=$SALT" > /salt.txt
ARG BLOAT_MB=0
ARG BLOAT_SALT=none
RUN echo "bloat=$BLOAT_SALT" > /bloat.txt && if [ "$BLOAT_MB" -gt 0 ]; then dd if=/dev/urandom of=/bloat.bin bs=1M count="$BLOAT_MB"; fi
