import unittest
from unittest.mock import Mock

from main import DeviceCommandWorker


class DeviceCommandWorkerTests(unittest.TestCase):
    def test_pending_volume_updates_are_coalesced(self):
        worker = DeviceCommandWorker(Mock(), max_queue_size=2)
        topic = "crestroncontrol/volume/Living_Room"

        self.assertTrue(worker.submit(topic, "-30"))
        self.assertTrue(worker.submit(topic, "-20"))
        self.assertTrue(worker.submit(topic, "-10"))

        self.assertEqual(worker._commands.qsize(), 1)
        self.assertEqual(worker._commands.get_nowait().payload, "-10")

    def test_non_volume_queue_is_bounded(self):
        worker = DeviceCommandWorker(Mock(), max_queue_size=1)

        self.assertTrue(worker.submit("crestroncontrol/mute/Office", "ON"))
        self.assertFalse(worker.submit("crestroncontrol/mute/Office", "OFF"))


if __name__ == "__main__":
    unittest.main()
