# Prometheus as its own Render service, pinned to the version used locally.

FROM prom/prometheus:v3.13.1

COPY prometheus.yml /etc/prometheus/prometheus.yml

# Base image runs as "nobody"; briefly switch to root to install the entrypoint.
USER root
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER nobody

ENTRYPOINT ["/entrypoint.sh"]
