import argparse
import json
import math
import random
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app


def args(**values):
    return argparse.Namespace(demo=False, osc_host='127.0.0.1', osc_port=9000, receive_port=9001, **values)


class BridgeTests(unittest.TestCase):
    def test_euler_roundtrip_including_gimbal_lock(self):
        rng = random.Random(32)
        angles = [[rng.uniform(-89, 89), rng.uniform(-180, 180), rng.uniform(-180, 180)] for _ in range(300)]
        angles += [[90, 40, 20], [-90, 40, 20]]
        for a in angles:
            before = app.rotation(a)
            after = app.rotation(app.euler(before))
            self.assertLess(max(abs(before[i][j] - after[i][j]) for i in range(3) for j in range(3)), 1e-6)

    def test_handedness(self):
        p, r = app.unity_pose([[1, 0, 0, 2], [0, 1, 0, 3], [0, 0, 1, -4]])
        self.assertEqual(p, [2, 3, 4])
        self.assertEqual(r, app.rotation([0, 0, 0]))

    def test_calibration_translation_osc_and_tracking_loss(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(('127.0.0.1', 0))
        receiver.settimeout(0.15)
        cfg = args()
        cfg.osc_port = receiver.getsockname()[1]
        b = app.Bridge(cfg)
        self.addCleanup(b.sender.close)
        self.addCleanup(receiver.close)
        b.tracked = ([0, 0, 0], app.rotation([0, 0, 0]))
        b.action({'action': 'calibrate', 'pose': [10, 2, 30, 0, 90, 0]})
        b.tracked = ([0, 0, 0.2], app.rotation([0, 0, 0]))
        b.action({'action': 'heading'})
        b.action({'action': 'follow', 'enabled': True})
        b.update([], ([0, 0, 1], app.rotation([0, 30, 0])))
        self.assertAlmostEqual(b.output[0], 11)
        self.assertAlmostEqual(b.output[2], 30)
        self.assertAlmostEqual(b.output[4], 120)
        packets = [list(app.decode_osc(receiver.recv(4096)))[0] for _ in range(5)]
        self.assertEqual(packets[-1][0], '/usercamera/Pose')
        self.assertEqual(len(packets[-1][1]), 6)
        b.update([], None)
        self.assertFalse(b.follow)
        b.update([], ([0, 0, 2], app.rotation([0, 0, 0])))
        with self.assertRaises(socket.timeout):
            receiver.recv(4096)
        with self.assertRaises(ValueError):
            b.action({'action': 'calibrate', 'pose': [math.nan] * 6})

    def test_landscape_mount_translation_does_not_rotate_or_swap_height(self):
        cfg = args()
        cfg.demo = True
        b = app.Bridge(cfg)
        self.addCleanup(b.sender.close)
        # Arbitrary mounted tracker tilt: this formerly tilted the entire play space.
        r0 = app.rotation([85, 33, 28])
        p0 = [1, 2, 3]
        b.tracked = (p0, r0)
        b.action({'action': 'calibrate', 'pose': [10, 4, 20, 27, 42, 45]})
        with self.assertRaises(ValueError):
            b.action({'action': 'follow', 'enabled': True})
        b.tracked = ([1, 2, 3.2], r0)
        b.action({'action': 'heading'})
        b.action({'action': 'follow', 'enabled': True})
        b.update([], ([1, 2.3, 3], r0))
        self.assertAlmostEqual(b.output[0], 10)
        self.assertAlmostEqual(b.output[1], 4.3)
        self.assertAlmostEqual(b.output[2], 20)
        self.assertAlmostEqual(b.output[3], 0)
        self.assertAlmostEqual(b.output[4], 42)
        self.assertAlmostEqual(b.output[5], 0)
        for displacement in ([0.2, 0, 0], [0, 0, 0.2], [-0.3, 0.2, 0.1]):
            b.update([], ([p0[i]+displacement[i] for i in range(3)], r0))
            expected = app.mv(app.rotation([0, 42, 0]), displacement)
            for i in range(3):
                self.assertAlmostEqual(b.output[i], [10, 4, 20][i] + expected[i])
            self.assertAlmostEqual(b.output[4], 42)
            self.assertAlmostEqual(b.output[3], 0)
            self.assertAlmostEqual(b.output[5], 0)

    def test_mount_rotates_about_phone_axes_not_tracker_axes(self):
        cfg = args()
        cfg.demo = True
        b = app.Bridge(cfg)
        self.addCleanup(b.sender.close)
        r0 = app.rotation([85, 33, 28])
        b.tracked = ([0, 1, 0], r0)
        b.action({'action': 'calibrate', 'pose': [0, 1, 0, 0, 42, 0]})
        b.tracked = ([0.2, 1, 0], r0)
        b.action({'action': 'heading'})
        b.action({'action': 'follow', 'enabled': True})
        space = b.anchor['space']
        initial_camera = app.rotation([0, 42, 0])
        for change in ([20, 0, 0], [0, 20, 0], [0, 0, 20]):
            phone_world_delta = app.mm(app.mm(initial_camera, app.rotation(change)), app.transpose(initial_camera))
            tracker_world_delta = app.mm(app.mm(app.transpose(space), phone_world_delta), space)
            b.update([], ([0, 1, 0], app.mm(tracker_world_delta, r0)))
            expected = app.mm(initial_camera, app.rotation(change))
            actual = app.rotation(b.output[3:])
            self.assertLess(max(abs(expected[i][j]-actual[i][j]) for i in range(3) for j in range(3)), 1e-6)
            self.assertEqual(b.output[:3], [0, 1, 0])

    def test_heading_rejects_vertical_or_insufficient_motion(self):
        cfg = args()
        cfg.demo = True
        b = app.Bridge(cfg)
        self.addCleanup(b.sender.close)
        b.tracked = ([0, 1, 0], app.rotation([0, 0, 0]))
        b.action({'action': 'calibrate', 'pose': [0, 1, 0, 0, 0, 0]})
        for p in ([0, 1.3, 0], [0, 1, 0.02], [0, 1.3, 0.1]):
            b.tracked = (p, app.rotation([0, 0, 0]))
            with self.assertRaises(ValueError):
                b.action({'action': 'heading'})

    def test_received_pose_and_stale_guard(self):
        reserved = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
        reserved.close()
        cfg = args()
        cfg.receive_port = port
        b = app.Bridge(cfg)
        thread = threading.Thread(target=b.receive)
        thread.start()
        try:
            message = app.osc('/usercamera/Pose', [1., 2., 3., 4., 5., 6.])
            for _ in range(30):
                b.sender.sendto(message, ('127.0.0.1', port))
                if b.camera is not None:
                    break
                time.sleep(0.02)
            self.assertEqual(b.camera, [1, 2, 3, 4, 5, 6])
            b.tracked = ([0, 0, 0], app.rotation([0, 0, 0]))
            b.action({'action': 'calibrate'})
            b.camera_at = 0
            with self.assertRaises(ValueError):
                b.action({'action': 'calibrate'})
        finally:
            b.stop.set()
            thread.join(1)
            b.sender.close()

    def test_http_access_and_actions(self):
        cfg = args()
        cfg.demo = True
        b = app.Bridge(cfg)
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.handler(b))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + '/api/status')
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            with urllib.request.urlopen(base + '/?token=' + b.token) as r:
                self.assertIn('VRC Phone Camera', r.read().decode())
            with urllib.request.urlopen(base + '/api/status?token=' + b.token) as r:
                self.assertTrue(json.load(r)['demo'])
            request = urllib.request.Request(base + '/api/action?token=' + b.token,
                data=json.dumps({'action': 'zoom', 'value': 45}).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as r:
                self.assertTrue(json.load(r)['ok'])
            request.data = json.dumps({'action': 'zoom', 'value': 151}).encode()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            b.sender.close()


if __name__ == '__main__':
    unittest.main()
