# Prometheus as its own small Render service.
#
# Scrapes the deployed ARGO API and remote_writes everything to Grafana Cloud.
# Pinned to the same version used locally so behaviour matches.

FROM prom/prometheus:v3.13.1

# Our scrape + remote_write config replaces the image's default one
COPY prometheus.yml /etc/prometheus/prometheus.yml

# The base image runs as "nobody"; switch to root just long enough to install
# the entrypoint script with the right permissions, then drop back.
USER root
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER nobody

# Render supplies $PORT at runtime; the entrypoint binds Prometheus to it.
ENTRYPOINT ["/entrypoint.sh"]
