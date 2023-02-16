# Copyright 2023 Guillermo
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import logging
import unittest

import ops.testing
from charm import KafkaExporterCharm
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

logger = logging.getLogger(__name__)


class TestCharm(unittest.TestCase):
    """Class to test the charm."""

    def setUp(self):
        # Enable more accurate simulation of container networking.
        # For more information, see https://juju.is/docs/sdk/testing#heading--simulate-can-connect
        ops.testing.SIMULATE_CAN_CONNECT = True
        self.addCleanup(setattr, ops.testing, "SIMULATE_CAN_CONNECT", False)

        self.harness = Harness(KafkaExporterCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_kafka_exporter_pebble_ready(self):
        """Test to check the plan created is the expected one."""
        # Expected plan after Pebble ready with default config
        expected_plan = {
            "services": {
                "kafka-exporter": {
                    "override": "replace",
                    "summary": "kafka-exporter service",
                    "command": "bin/kafka_exporter --kafka.server=kafka:9092",
                    "startup": "enabled",
                },
            },
        }

        self.harness.set_can_connect("kafka-exporter", True)
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        # Simulate the container coming up and emission of pebble-ready event
        self.harness.container_pebble_ready("kafka-exporter")
        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("kafka-exporter").to_dict()
        # Check we've got the plan we expected
        self.assertEqual(expected_plan, updated_plan)
        # Check the service was started
        service = self.harness.model.unit.get_container("kafka-exporter").get_service(
            "kafka-exporter"
        )
        self.assertTrue(service.is_running())
        # Ensure we set an ActiveStatus with no message
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    def test_kafka_exporter_service_not_started(self):
        """Test to check the plan created is the expected one."""
        # Expected plan after Pebble ready with default config
        expected_plan = {}
        error_message = (
            "Not kafka-endpoint added. Kafka endpoint needs to be added via relation or via config"
        )

        self.harness.set_can_connect("kafka-exporter", True)
        # Simulate the container coming up and emission of pebble-ready event
        self.harness.container_pebble_ready("kafka-exporter")
        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("kafka-exporter").to_dict()
        # Check we've got the plan we expected
        self.assertEqual(expected_plan, updated_plan)
        # Ensure we set an ActiveStatus with no message
        self.assertEqual(self.harness.model.unit.status, BlockedStatus(error_message))

    def test_config_changed_valid_can_connect(self):
        """Valid config change for kafka-uri parameter."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    def test_config_changed_valid_cannot_connect(self):
        """Test cannot connect to Pebble."""
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        # Check the charm is in WaitingStatus
        self.assertIsInstance(self.harness.model.unit.status, WaitingStatus)

    def test_config_kafka_uri_changed_invalid(self):
        """Valid config change for kafka-uri parameter."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"kafka-endpoint": "foobar"})
        # Check the charm is in BlockedStatus
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_config_log_changed_invalid(self):
        """Valid config change for log-level parameter."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"log-level": "foobar"})
        # Check the charm is in BlockedStatus
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_config_log_changed_no_kafka(self):
        """Valid config change for log-level parameter."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        # Trigger a config-changed event with an updated value
        self.harness.update_config({"log-level": "INFO"})
        # Check the charm is in BlockedStatus
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_no_config(self):
        """No database related or configured in the charm."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        self.harness.charm.on.config_changed.emit()
        # Check the charm is in BlockedStatus
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_kafka_relation(self):
        """No database related or configured in the charm."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        relation_id = self.harness.add_relation("kafka", "kafka")
        self.harness.add_relation_unit(relation_id, "kafka/0")
        self.harness.update_relation_data(relation_id, "kafka", {"host": "kafka", "port": "9092"})
        self.harness.charm.on.config_changed.emit()
        self.assertIsInstance(self.harness.model.unit.status, ActiveStatus)

    def test_kafka_relation_broken(self):
        """No database related or configured in the charm."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        relation_id = self.harness.add_relation("kafka", "kafka")
        self.harness.add_relation_unit(relation_id, "kafka/0")
        self.harness.update_relation_data(relation_id, "kafka", {"host": "kafka", "port": "9092"})
        self.harness.charm.on.config_changed.emit()
        self.assertIsInstance(self.harness.model.unit.status, ActiveStatus)
        self.harness.remove_relation(relation_id)
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_update_status_no_kafka(self):
        """update_status test Blocked because no Kafka DB."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        self.harness.charm.on.update_status.emit()
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_update_status_success(self):
        """update_status test successful."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        self.harness.charm.on.update_status.emit()
        self.assertIsInstance(self.harness.model.unit.status, ActiveStatus)

    def test_db_creation_config_first(self):
        """Connected to Kafka through config and relation."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        relation_id = self.harness.add_relation("kafka", "kafka")
        self.harness.add_relation_unit(relation_id, "kafka/0")
        self.harness.update_relation_data(relation_id, "kafka", {"host": "kafka", "port": "9092"})
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_db_duplicated_relation_first(self):
        """Connected to Kafka through config and relation."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        relation_id = self.harness.add_relation("kafka", "kafka")
        self.harness.add_relation_unit(relation_id, "kafka/0")
        self.harness.update_relation_data(relation_id, "kafka", {"host": "kafka", "port": "9092"})
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

    def test_from_duplicated_to_config_only(self):
        """Connected to Kafka through config and relation and then relation broken."""
        # Ensure the simulated Pebble API is reachable
        self.harness.set_can_connect("kafka-exporter", True)
        relation_id = self.harness.add_relation("kafka", "kafka")
        self.harness.add_relation_unit(relation_id, "kafka/0")
        self.harness.update_relation_data(relation_id, "kafka", {"host": "kafka", "port": "9092"})
        self.harness.update_config({"kafka-endpoint": "kafka:9092"})
        self.assertIsInstance(self.harness.model.unit.status, BlockedStatus)

        self.harness.remove_relation(relation_id)
        self.assertIsInstance(self.harness.model.unit.status, ActiveStatus)
