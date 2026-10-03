"""Windows integration: actual Spout shared texture -> bridge -> JPEG pixels."""
import threading
import time
import unittest
from test_bridge import args
import app

try:
    import SpoutGL
    import cv2
    import numpy as np
except ImportError:
    SpoutGL = None


@unittest.skipIf(SpoutGL is None, 'SpoutGL runtime required')
class SpoutIntegrationTests(unittest.TestCase):
    def test_direct_spout_pixels_and_resolution_change(self):
        cfg = args()
        cfg.demo = True
        bridge = app.Bridge(cfg)
        name = 'Codex-camera-integration-test'
        bridge.action({'action': 'spout', 'sender': name})
        thread = threading.Thread(target=bridge.video)
        thread.start()
        sender = SpoutGL.SpoutSender()
        sender.setSenderName(name)
        try:
            for width, height in [(160, 120), (320, 240)]:
                pixels = np.zeros((height, width, 4), dtype=np.uint8)
                pixels[:] = [250, 40, 30, 255]
                deadline = time.monotonic() + 5
                image = None
                while time.monotonic() < deadline:
                    sender.sendImage(pixels, width, height, SpoutGL.enums.GL_RGBA, False, 0)
                    time.sleep(0.04)
                    if bridge.frame:
                        image = cv2.imdecode(np.frombuffer(bridge.frame, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if image.shape[:2] == (height, width):
                            break
                self.assertIsNotNone(image, bridge.video_error)
                self.assertEqual(image.shape[:2], (height, width), bridge.video_error)
                self.assertLess(np.max(np.abs(image[20, 20].astype(int) - [30, 40, 250])), 6)
                self.assertTrue(bridge.status()['videoFresh'])
                self.assertIn(name, bridge.video_info)
            bridge.action({'action': 'video', 'index': None})
            time.sleep(0.15)
            self.assertEqual(bridge.video_source, 'off')
            self.assertIsNone(bridge.frame)
        finally:
            bridge.stop.set()
            thread.join(3)
            sender.releaseSender()
            bridge.sender.close()


if __name__ == '__main__':
    unittest.main()
