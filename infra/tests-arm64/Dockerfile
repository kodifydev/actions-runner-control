FROM ghcr.io/actions/actions-runner:2.337.0
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential python3-venv python3-pip python-is-python3 libpq-dev zip rsync zstd gnupg ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && printf 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main\n' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends postgresql-18 postgresql-18-pgvector \
    && rm -rf /var/lib/apt/lists/* /var/lib/postgresql/* \
    && mkdir -p /opt/hostedtoolcache /home/runner/test-postgres /home/runner/test-postgres-socket \
    && chown -R runner:runner /opt/hostedtoolcache /home/runner
COPY entrypoint.sh /usr/local/bin/tests-entrypoint
RUN chmod 0755 /usr/local/bin/tests-entrypoint
ENV PATH=/usr/lib/postgresql/18/bin:$PATH
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
ENV PYTEST_XDIST_AUTO_NUM_WORKERS=2 UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_INSTALLS=2
USER runner
WORKDIR /home/runner
ENTRYPOINT ["/usr/local/bin/tests-entrypoint"]
