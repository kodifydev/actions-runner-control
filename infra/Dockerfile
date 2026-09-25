FROM ghcr.io/actions/actions-runner@sha256:e5496277be5d09bc968b3d64911b74e219ac4a3f2edce956a3ecf9271bea1ef4
USER root
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential python3-venv python3-pip zip rsync zstd gnupg ca-certificates \
    && curl --fail --silent --show-error --location https://packages.microsoft.com/keys/microsoft.asc \
       | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && rm -rf /var/lib/apt/lists/*
COPY azure-cli.sources /etc/apt/sources.list.d/azure-cli.sources
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends azure-cli \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /opt/hostedtoolcache /var/lib/kodifyci/data/work \
    && chown runner:runner /opt/hostedtoolcache \
    && chmod 0777 /var/lib/kodifyci/data/work
COPY --chmod=0755 runner-entrypoint.sh /usr/local/bin/kodify-runner-entrypoint
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache
ENV AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
ENV DOCKER_HOST=unix:///var/run/docker.sock
LABEL kodify.runner.base=true
USER runner
WORKDIR /home/runner
ENTRYPOINT ["/usr/local/bin/kodify-runner-entrypoint"]
