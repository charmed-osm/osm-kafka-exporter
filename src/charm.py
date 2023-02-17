#!/usr/bin/env python3
# Copyright 2023 Guillermo
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import logging
import time

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.kafka_k8s.v0.kafka import KafkaEvents, KafkaRequires
from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from charms.osm_libs.v0.utils import (
    CharmError,
    check_container_ready,
    check_service_active,
)
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ChangeError

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

PORT = 9308

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]


class KafkaEndpoint:
    """Kafka endpoint class."""

    def __init__(self, host: str, port: str) -> None:
        self.host = host
        self.port = port


class KafkaExporterCharm(CharmBase):
    """Charm the service."""

    on = KafkaEvents()

    def __init__(self, *args):
        super().__init__(*args)
        self.kafka_endpoint = None
        self.kafka_client = KafkaRequires(self)
        self.pebble_service_name = "kafka-exporter"
        self.container_name = "kafka-exporter"
        self.container = self.unit.get_container("kafka-exporter")
        self.ingress = IngressRequires(
            self,
            {
                "service-hostname": self.model.config.get("external-hostname"),
                "service-name": self.app.name,
                "service-port": PORT,
            },
        )
        jobs = [{"static_configs": [{"targets": [f"*:{PORT}"]}]}]
        self.metrics_consumer = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=jobs,
            refresh_event=self.on.config_changed,
        )
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        self.framework.observe(self.on.kafka_available, self._on_kafka_available)
        self.framework.observe(
            self.on.kafka_exporter_pebble_ready, self._on_kafka_exporter_pebble_ready
        )
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on["kafka"].relation_broken, self._on_db_relation_broken)

    def _on_kafka_exporter_pebble_ready(self, event):
        """Define and start a workload using the Pebble API.

        Change this example to suit your needs. You'll need to specify the right entrypoint and
        environment configuration for your specific workload.

        Learn more about interacting with Pebble at at https://juju.is/docs/sdk/pebble.
        """
        try:
            self.kafka_endpoint = self._get_kafka_endpoint(event)
            # Add initial Pebble config layer using the Pebble API
            self.container.add_layer("kafka-exporter", self._pebble_layer, combine=True)
            # Make Pebble reevaluate its plan, ensuring any services are started if enabled.
            self.container.replan()
            # Learn more about statuses in the SDK docs:
            # https://juju.is/docs/sdk/constructs#heading--statuses
            self.unit.status = ActiveStatus()
        except CharmError as error:
            logger.warning(error.message)
            self.unit.status = error.status

    def _configure_service(self, event, retry=3) -> None:
        try:
            if retry:
                if self.container.can_connect():
                    # Push an updated layer with the new config
                    self.container.add_layer("kafka-exporter", self._pebble_layer, combine=True)
                    self.container.replan()
                    self.unit.status = ActiveStatus()
                else:
                    # We were unable to connect to the Pebble API, so we defer this event
                    event.defer()
                    self.unit.status = WaitingStatus("waiting for Pebble API")
        except ChangeError as error:
            self._retry_configure_service(event, retry, error)

    def _retry_configure_service(self, event, retry, error) -> None:
        """Retry to configure the service in case it is not ready yet."""
        logger.warning("Cannot start the service %s", error)
        logger.warning("Retry %s times", retry)
        time.sleep(5)
        retry -= 1
        self._configure_service(event, retry)

    def _validate_config(self) -> None:
        """Validate charm configuration.

        Raises:
            CharmError: if charm configuration is invalid.
        """
        if self.config["log-level"].upper() not in [
            "TRACE",
            "DEBUG",
            "INFO",
            "WARN",
            "ERROR",
            "FATAL",
        ]:
            self.unit.status = BlockedStatus(
                f"invalid log level: {self.model.config['log-level'].upper()}"
            )
            raise CharmError("invalid value for log-level option")

        if kafkaendpoint := self.model.config.get("kafka-endpoint"):
            if len(kafkaendpoint.split(":")) != 2:
                self.unit.status = BlockedStatus("Wrong kafka-endpoint format")
                raise CharmError(
                    "Wrong kafka-endpoint format: value must be in the format <host>:<port>"
                )

    def _get_kafka_config(self):
        try:
            self._validate_config()
            return self.model.config.get("kafka-endpoint")
        except CharmError as error:
            raise CharmError(error.message) from error

    def _get_kafka_relation(self, event):
        if type(event).__name__ == "RelationBrokenEvent":
            return None
        if self.kafka_client.host and self.kafka_client.port:
            return f"{self.kafka_client.host}:{self.kafka_client.port}"
        return None

    def _on_config_changed(self, event=None) -> None:
        """Handle changed configuration."""
        try:
            # Fetch the new config value
            self.kafka_endpoint = self._get_kafka_endpoint(event)
            self._configure_service(event)
            self._update_ingress_config()
        except CharmError as error:
            logger.warning(error.message)
            self._stop_container()
            self.unit.status = error.status

    def _on_update_status(self, event=None) -> None:
        """Handle for the update-status event."""
        try:
            self.kafka_endpoint = self._get_kafka_endpoint(event)
            check_container_ready(self.container)
            check_service_active(self.container, self.pebble_service_name)
            self.unit.status = ActiveStatus()
        except CharmError as error:
            logger.warning(error.message)
            self._stop_container()
            self.unit.status = error.status

    def _stop_container(self) -> None:
        """Stop workload container."""
        try:
            check_service_active(self.container, self.pebble_service_name)
            check_container_ready(self.container)
            self.container.stop(self.container_name)
        except CharmError as error:
            logger.warning(error.message)
            service_not_configured = f"{self.pebble_service_name} service not configured yet"
            if error.message != service_not_configured:
                self.container.stop(self.container_name)

    def _on_db_relation_broken(self, event) -> None:
        """Handle relation broken event."""
        try:
            self.kafka_endpoint = self._get_kafka_endpoint(event)
            self._configure_service(event)
        except CharmError as error:
            self._stop_container()
            self.unit.status = error.status

    def _on_kafka_available(self, event) -> None:
        """Event triggered when a database was created for this application via relation."""
        try:
            self.kafka_endpoint = self._get_kafka_endpoint()
            check_container_ready(self.container)
            self._configure_service(event)
            self._on_update_status()
        except CharmError as error:
            self._stop_container()
            self.unit.status = error.status

    def _update_ingress_config(self) -> None:
        """Update ingress config in relation."""
        ingress_config = {
            "service-hostname": self.model.config.get("external-hostname"),
        }
        logger.debug(f"updating ingress-config: {ingress_config}")
        self.ingress.update_config(ingress_config)

    def _get_kafka_endpoint(self, event=None) -> str:
        """Return kafka endpoint.

        Raises:
            CharmError: if no kafka endpoint.
        """
        relation = self._get_kafka_relation(event)

        if configuration := self._get_kafka_config():
            if relation:
                raise CharmError("Kafka cannot added via relation and via config at the same time")
            return configuration
        if not relation:
            raise CharmError(
                "Not kafka-endpoint added. Kafka endpoint needs to be added via relation or via config"
            )
        return relation

    @property
    def _pebble_layer(self):
        """Return a dictionary representing a Pebble layer."""
        return {
            "summary": "kafka-exporter layer",
            "description": "pebble config layer for kafka-exporter",
            "services": {
                self.pebble_service_name: {
                    "override": "replace",
                    "summary": "kafka-exporter service",
                    "command": f"bin/kafka_exporter --kafka.server={self.kafka_endpoint}",
                    "startup": "enabled",
                }
            },
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "tcp": {
                        "port": PORT,
                    },
                },
            },
        }


if __name__ == "__main__":  # pragma: nocover
    main(KafkaExporterCharm)
