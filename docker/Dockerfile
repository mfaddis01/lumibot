# Use ONLY a clean committed context emitted by packaging_context.py.
# Single linux/amd64 manifest, NOT the multi-platform index or config ID.
FROM --platform=linux/amd64 python@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4 AS fetch
ENV LUMIBOT_DISABLE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /input
COPY source/ /input/source/
COPY docker/ /input/docker/
COPY packaging-inventory.json /input/packaging-inventory.json
ARG CONTEXT_SHA256
RUN --network=none python /input/docker/packaging_build.py verify --root /input --digest "$CONTEXT_SHA256"
# This is the ONLY network-enabled RUN: public archive bytes, never setup/import.
RUN python /input/docker/packaging_build.py fetch --root /input --digest "$CONTEXT_SHA256"

FROM --platform=linux/amd64 python@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4 AS build
ENV LUMIBOT_DISABLE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /input
COPY --from=fetch /input/ /input/
COPY --from=fetch /downloads/ /downloads/
ARG CONTEXT_SHA256
RUN --network=none python /input/docker/packaging_build.py build --root /input --digest "$CONTEXT_SHA256"

FROM --platform=linux/amd64 python@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4 AS runtime
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin \
    HOME=/tmp LUMIBOT_DISABLE_DOTENV=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/cache
COPY --from=build /opt/venv/ /opt/venv/
COPY --from=build /opt/personal-trading/ /opt/personal-trading/
# Runtime has neither pip in its venv nor the base's global pip/ensurepip.
RUN --network=none rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.12 \
    /usr/local/lib/python3.12/site-packages/pip \
    /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
    /usr/local/lib/python3.12/ensurepip
WORKDIR /opt/personal-trading
USER 10001:10001
ENTRYPOINT ["/opt/venv/bin/python", "-m", "personal_trading"]
CMD ["--help"]
