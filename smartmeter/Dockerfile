# The base image is pinned explicitly. Since Supervisor 2026.04.0 the BUILD_FROM
# build argument is no longer supplied, and build.yaml is gone, so a Dockerfile
# that relies on either does not build. This tag is multi-architecture and
# covers both aarch64 and amd64.
FROM ghcr.io/home-assistant/base-python:3.12-alpine3.23-2026.06.1

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=0.2.0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/smartmeter/app

WORKDIR /opt/smartmeter

# Installed before the application is copied, so an edit to the Python code does
# not reinstall the dependencies.
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY app/ /opt/smartmeter/app/
COPY suppliers/ /opt/smartmeter/suppliers/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

LABEL \
    io.hass.name="Austrian Smart Meter" \
    io.hass.description="Reads the customer interface of an Austrian smart meter" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.version="${BUILD_VERSION}" \
    org.opencontainers.image.source="https://github.com/Alexander423/ha-smartmeter-at" \
    org.opencontainers.image.licenses="MIT"

CMD [ "/run.sh" ]
