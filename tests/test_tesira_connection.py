import time
import unittest
from unittest.mock import patch

import tesira_controller
from config_loader import TesiraUnit
from tesira_controller import TesiraConnection


class FakeTransport:
    def is_active(self):
        return True

    def is_authenticated(self):
        return True


class FakeClient:
    def get_transport(self):
        return FakeTransport()

    def close(self):
        pass


class DelayedChannel:
    def __init__(self, replies):
        self._reply_specs = replies
        self._replies = []
        self.sent = []
        self.closed = False
        self.eof_received = False

    def sendall(self, payload):
        self.sent.append(payload)
        started = time.monotonic()
        self._replies = [
            (started + delay, reply.encode()) for delay, reply in self._reply_specs
        ]

    def recv_ready(self):
        return bool(self._replies and self._replies[0][0] <= time.monotonic())

    def recv(self, size):
        _, reply = self._replies.pop(0)
        return reply

    def close(self):
        self.closed = True


def connected_with(channel):
    connection = TesiraConnection(TesiraUnit(id="test", host="127.0.0.1"))
    connection._client = FakeClient()
    connection._channel = channel
    return connection


class TesiraConnectionTests(unittest.TestCase):
    def test_waits_for_the_complete_response_line(self):
        channel = DelayedChannel([
            (0, "+OK"),
            (0.02, "\r\n"),
        ])
        connection = connected_with(channel)

        with patch.object(tesira_controller, "COMMAND_RESPONSE_TIMEOUT", 0.2):
            success, response = connection.send_command("split-response")

        self.assertTrue(success)
        self.assertEqual(response, "+OK\r\n")

    def test_waits_for_every_delayed_batch_response(self):
        channel = DelayedChannel([
            (0.02, "+OK\r\n"),
            (0.04, "+OK\r\n"),
        ])
        connection = connected_with(channel)

        with patch.object(tesira_controller, "COMMAND_RESPONSE_TIMEOUT", 0.2):
            self.assertTrue(connection.send_commands_batch(["first", "second"]))

        self.assertEqual(channel.sent, [b"first\nsecond\n"])
        self.assertFalse(channel._replies)

    def test_batch_fails_when_any_command_is_rejected(self):
        channel = DelayedChannel([
            (0, "+OK\r\n"),
            (0, "-ERR invalid command\r\n"),
        ])
        connection = connected_with(channel)

        self.assertFalse(connection.send_commands_batch(["first", "second"]))

    def test_timeout_reconnects_and_retries_on_a_fresh_channel(self):
        first_channel = DelayedChannel([])
        second_channel = DelayedChannel([(0, "+OK\r\n")])
        connection = connected_with(first_channel)
        reconnect_count = 0

        def reconnect():
            nonlocal reconnect_count
            reconnect_count += 1
            connection._client = FakeClient()
            connection._channel = second_channel
            return True

        with (
            patch.object(tesira_controller, "COMMAND_RESPONSE_TIMEOUT", 0.03),
            patch.object(connection, "connect", side_effect=reconnect),
        ):
            success, _ = connection.send_command("retry-me")

        self.assertTrue(success)
        self.assertEqual(reconnect_count, 1)
        self.assertTrue(first_channel.closed)
        self.assertEqual(second_channel.sent, [b"retry-me\n"])


if __name__ == "__main__":
    unittest.main()
