#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -e

# The broker is not a user setting. Supervisor knows where it is and which
# credentials this add-on may use, and bashio asks it.
if ! bashio::services.available "mqtt"; then
    bashio::log.error "No MQTT broker is available."
    bashio::log.error "Install the Mosquitto broker add-on, start it, then start this add-on."
    exit 1
fi

MQTT_HOST=$(bashio::services mqtt "host")
MQTT_PORT=$(bashio::services mqtt "port")
MQTT_USERNAME=$(bashio::services mqtt "username")
MQTT_PASSWORD=$(bashio::services mqtt "password")
export MQTT_HOST MQTT_PORT MQTT_USERNAME MQTT_PASSWORD

if bashio::config.has_value "log_level"; then
    bashio::log.level "$(bashio::config 'log_level')"
fi

bashio::log.info "Timezone is ${TZ:-not set}"

# The application reads /data/options.json itself, so nothing else is passed on
# the command line. exec, so signals from Supervisor reach Python directly and
# a stop takes a moment rather than ten seconds.
exec python3 -m ha_smartmeter run
