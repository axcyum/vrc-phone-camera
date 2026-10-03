"""Tracked phone camera bridge. Standard-library OSC/HTTP, optional OpenVR/OpenCV."""
import argparse
import json
import math
import secrets
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(a):
    return [list(x) for x in zip(*a)]


def mv(a, v):
    return [sum(a[i][k] * v[k] for k in range(3)) for i in range(3)]


def rotation(angles):
    x, y, z = map(math.radians, angles)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    return mm(mm([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]],
                 [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]),
              [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])


def euler(r):
    x = math.asin(max(-1, min(1, -r[1][2])))
    if abs(math.cos(x)) > 1e-6:
        y, z = math.atan2(r[0][2], r[2][2]), math.atan2(r[1][0], r[1][1])
    else:
        y, z = math.atan2(-r[2][0], r[0][0]), 0
    return list(map(math.degrees, (x, y, z)))


def unity_pose(matrix):
    # OpenVR: right handed, -Z forward. Unity: left handed, +Z forward.
    signs = [1, 1, -1]
    return ([float(matrix[i][3]) * signs[i] for i in range(3)],
            [[float(matrix[i][j]) * signs[i] * signs[j] for j in range(3)] for i in range(3)])


def osc_string(value):
    data = value.encode('utf-8') + b'\0'
    return data + b'\0' * (-len(data) % 4)


def osc(address, values):
    tags, payload = ',', b''
    for value in values:
        if isinstance(value, bool):
            tags += 'T' if value else 'F'
        elif isinstance(value, int):
            tags += 'i'
            payload += struct.pack('>i', value)
        else:
            tags += 'f'
            payload += struct.pack('>f', float(value))
    return osc_string(address) + osc_string(tags) + payload


def decode_osc(data):
    if data.startswith(b'#bundle\0'):
        offset = 16
        while offset + 4 <= len(data):
            size = struct.unpack_from('>I', data, offset)[0]
            offset += 4
            if size > len(data) - offset:
                raise ValueError('Invalid OSC bundle')
            yield from decode_osc(data[offset:offset + size])
            offset += size
        return
    def string(offset):
        end = data.index(b'\0', offset)
        return data[offset:end].decode('utf-8'), (end + 4) & ~3
    address, offset = string(0)
    tags, offset = string(offset)
    values = []
    for tag in tags[1:]:
        if tag in 'if':
            values.append(struct.unpack_from('>' + tag, data, offset)[0])
            offset += 4
        elif tag in 'TF':
            values.append(tag == 'T')
        else:
            return
    yield address, values


class Bridge:
    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.token = secrets.token_urlsafe(24)
        self.trackers = []
        self.devices = []
        self.rescan = threading.Event()
        self.selected = ''
        self.tracked = None
        self.anchor = None
        self.follow = False
        self.camera = None
        self.camera_at = 0
        self.output = None
        self.space_yaw = 0.0
        self.heading_ready = False
        self.error = ''
        self.osc_error = ''
        self.video_error = '映像OFF。Spout出力を選んで「Spout映像ON」を押してください。'
        self.video_source = 'off'
        self.spout_name = ''
        self.spout_senders = []
        self.video_info = ''
        self.video_index = None
        self.video_generation = 0
        self.frame = None
        self.frame_at = 0

    def send(self, address, values):
        if not self.args.demo:
            self.sender.sendto(osc(address, values), (self.args.osc_host, self.args.osc_port))

    def receive(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            receiver.bind(('127.0.0.1', self.args.receive_port))
            receiver.settimeout(0.5)
            while not self.stop.is_set():
                try:
                    packet, _ = receiver.recvfrom(65535)
                    for address, values in decode_osc(packet):
                        if address == '/usercamera/Pose' and len(values) == 6 and all(math.isfinite(v) for v in values):
                            with self.lock:
                                self.camera = values
                                self.camera_at = time.monotonic()
                except socket.timeout:
                    pass
                except (ValueError, struct.error, UnicodeError):
                    pass
        except OSError as exc:
            self.osc_error = f'OSC受信エラー: {exc}（他のOSCアプリが9001を使用していないか確認）'
        finally:
            receiver.close()

    def update(self, trackers, pose):
        with self.lock:
            self.trackers = trackers
            self.tracked = pose
            if pose is None:
                # Explicit restart is required after tracking loss.
                self.follow = False
                return
            if self.follow and self.anchor:
                p, r = pose
                a = self.anchor
                # World alignment preserves gravity; mounting belongs on the RIGHT.
                # Rcamera = Rspace * Rtracker * Rmount. Never use Rmount for translation.
                delta = mv(a['space'], [p[i] - a['position'][i] for i in range(3)])
                self.output = [a['camera'][i] + delta[i] for i in range(3)] + euler(mm(mm(a['space'], r), a['mount']))
                self.send('/usercamera/Pose', self.output)

    def tracking(self):
        if self.args.demo:
            start = time.monotonic()
            self.selected = 'DEMO'
            self.camera = [0, 1.5, 0, 0, 0, 0]
            while not self.stop.wait(1 / 60):
                t = time.monotonic() - start
                self.camera_at = time.monotonic()
                self.update([{'serial': 'DEMO', 'valid': True}],
                            ([0.15 * math.sin(t), 1.5, 0], rotation([0, 15 * math.sin(t / 2), 0])))
            return
        while not self.stop.is_set():
            initialized = False
            try:
                import openvr
                vr = openvr.init(openvr.VRApplication_Background)
                initialized = True
                poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
                event = openvr.VREvent_t()
                self.rescan.clear()
                empty_since = time.monotonic()
                while not self.stop.wait(1 / 60):
                    # Background clients must consume device activation/deactivation events too.
                    for _ in range(128):
                        if not vr.pollNextEvent(event):
                            break
                    vr.getDeviceToAbsoluteTrackingPose(openvr.TrackingUniverseStanding, 0, poses)
                    trackers, chosen = [], None
                    devices = []
                    for i, pose in enumerate(poses):
                        if not pose.bDeviceIsConnected:
                            continue
                        kind = vr.getTrackedDeviceClass(i)
                        try:
                            serial = vr.getStringTrackedDeviceProperty(i, openvr.Prop_SerialNumber_String)
                        except Exception:
                            serial = f'device-{i}'
                        devices.append({'serial': serial, 'class': kind, 'valid': bool(pose.bPoseIsValid)})
                        if kind != openvr.TrackedDeviceClass_GenericTracker:
                            continue
                        trackers.append({'serial': serial, 'valid': bool(pose.bPoseIsValid)})
                        if serial == self.selected and pose.bPoseIsValid:
                            chosen = unity_pose(pose.mDeviceToAbsoluteTracking)
                    self.error = ''
                    self.devices = devices
                    self.update(trackers, chosen)
                    if trackers:
                        empty_since = time.monotonic()
                    if self.rescan.is_set() or time.monotonic() - empty_since > 5:
                        break  # Refresh OpenVR after startup without devices / hotplug.
            except Exception as exc:
                with self.lock:
                    self.error = f'SteamVR: {exc}'
                    self.update([], None)
                self.stop.wait(3)
            finally:
                if initialized:
                    openvr.shutdown()
            self.stop.wait(0.25)

    def video(self):
        cap, receiver, generation, buffer = None, None, -1, None
        scan_at = 0
        try:
            import cv2
            import numpy as np
            try:
                import SpoutGL
                receiver = SpoutGL.SpoutReceiver()
            except ImportError:
                SpoutGL = None
            while not self.stop.is_set():
                with self.lock:
                    index, current, source, wanted = self.video_index, self.video_generation, self.video_source, self.spout_name
                if receiver and time.monotonic() - scan_at > 1:
                    self.spout_senders = list(receiver.getSenderList())
                    scan_at = time.monotonic()
                if generation != current:
                    if cap is not None:
                        cap.release()
                    cap = None
                    if receiver:
                        receiver.releaseReceiver()
                    buffer = None
                    generation = current
                    if source == 'spout':
                        if receiver:
                            receiver.setReceiverName(wanted)
                        else:
                            self.video_error = 'SpoutGLがありません。start.cmdでPython 3.12/3.13環境をセットアップしてください。'
                    elif source == 'camera' and index is not None:
                        try:
                            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                            if not cap.isOpened():
                                raise RuntimeError('カメラを開けません。デバイス番号を変更してください。')
                            self.video_error = ''
                        except Exception as exc:
                            self.video_error = str(exc)
                            if cap is not None:
                                cap.release()
                            cap = None
                frame = None
                if source == 'spout' and receiver:
                    if not wanted:
                        matches = [n for n in self.spout_senders if 'vrchat' in n.lower()]
                        if not matches:
                            self.video_error = 'VRChatのSpout出力を待っています。カメラUIのSpout StreamをONにしてください。'
                            self.stop.wait(0.1)
                            continue
                        wanted = matches[0]
                        with self.lock:
                            self.spout_name = wanted
                        receiver.setReceiverName(wanted)
                    result = receiver.receiveImage(buffer, SpoutGL.enums.GL_RGBA, False, 0)
                    width, height = receiver.getSenderWidth(), receiver.getSenderHeight()
                    if receiver.isUpdated() or (buffer is None and width and height):
                        # Spout requires buffer reallocation on first connection / resolution change.
                        if not 0 < width <= 8192 or not 0 < height <= 8192:
                            raise ValueError('Spoutの解像度が対応範囲外です。')
                        buffer = np.zeros((height, width, 4), dtype=np.uint8)
                        self.stop.wait(0.01)
                        continue
                    if result and buffer is not None and receiver.isFrameNew() and not SpoutGL.helpers.isBufferEmpty(buffer):
                        frame = cv2.cvtColor(buffer, cv2.COLOR_RGBA2BGR)
                        self.video_info = f'Spout: {wanted} ({width}×{height})'
                    elif not receiver.isConnected():
                        self.video_error = f'Spout出力「{wanted}」は未接続です。Spout Streamを確認してください。'
                elif cap is not None:
                    ok, candidate = cap.read()
                    if ok:
                        frame = candidate
                        self.video_info = f'映像デバイス {index}'
                    else:
                        self.video_error = '映像を受信できません。映像デバイスを確認してください。'
                else:
                    self.stop.wait(0.1)
                    continue
                if frame is not None:
                    height, width = frame.shape[:2]
                    if width > 1280:
                        frame = cv2.resize(frame, (1280, round(height * 1280 / width)), interpolation=cv2.INTER_AREA)
                    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                    if ok:
                        with self.lock:
                            self.frame, self.frame_at = jpeg.tobytes(), time.monotonic()
                            self.video_error = ''
                self.stop.wait(1 / 24)
        except Exception as exc:
            self.video_error = f'映像処理エラー: {exc}'
        finally:
            if cap is not None:
                cap.release()
            if receiver:
                receiver.releaseReceiver()

    def status(self):
        with self.lock:
            raw = self.tracked[0] + euler(self.tracked[1]) if self.tracked else None
            delta = None
            if self.tracked and self.anchor:
                delta = mv(self.anchor['space'], [self.tracked[0][i] - self.anchor['position'][i] for i in range(3)])
            return dict(demo=self.args.demo, trackers=self.trackers, selected=self.selected,
                        devices=self.devices,
                        rawTracker=raw, movement=delta, headingReady=self.heading_ready, spaceYaw=self.space_yaw,
                        valid=self.tracked is not None, calibrated=self.anchor is not None,
                        following=self.follow, camera=self.camera,
                        cameraFresh=time.monotonic() - self.camera_at < 5,
                        output=self.output, error=self.error, oscError=self.osc_error,
                        spoutSenders=self.spout_senders, spoutName=self.spout_name,
                        videoSource=self.video_source, videoInfo=self.video_info,
                        videoError=self.video_error, videoFresh=time.monotonic() - self.frame_at < 2)

    def action(self, data):
        with self.lock:
            action = data['action']
            if action == 'rescan':
                self.follow = False
                self.anchor = None
                self.heading_ready = False
                self.tracked = None
                self.rescan.set()
            elif action == 'select':
                serial = str(data['serial'])
                if serial not in [t['serial'] for t in self.trackers]:
                    raise ValueError('接続中のトラッカーを選択してください。')
                self.selected, self.anchor, self.follow, self.tracked = serial, None, False, None
                self.heading_ready = False
            elif action == 'calibrate':
                if self.follow:
                    raise ValueError('追従を停止してから基準合わせしてください。')
                if self.tracked is None:
                    raise ValueError('トラッカーの位置を取得できていません。')
                supplied = data.get('pose')
                camera = supplied if supplied is not None else self.camera
                if supplied is None and time.monotonic() - self.camera_at > 5:
                    raise ValueError('カメラ姿勢が未受信／古い状態です。VRChat内でカメラを動かすか、手入力してください。')
                if not isinstance(camera, list) or len(camera) != 6 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in camera):
                    raise ValueError('位置XYZ・回転XYZの6数値が必要です。')
                p, r = self.tracked
                # Neutral hold: screen toward user, phone landscape, tracker on top.
                # Level the virtual camera instead of preserving an accidental roll/pitch.
                camera = camera[:3] + [0.0, camera[4], 0.0]
                space = rotation([0, self.space_yaw, 0])
                mount = mm(mm(transpose(r), transpose(space)), rotation(camera[3:]))
                self.anchor = dict(position=p[:], rotation=r, camera=camera[:], space=space, mount=mount)
                self.heading_ready = False
                self.output = camera[:]
            elif action == 'heading':
                if self.follow or not self.anchor or self.tracked is None:
                    raise ValueError('停止して、先に基準合わせをしてください。')
                a = self.anchor
                d = [self.tracked[0][i] - a['position'][i] for i in range(3)]
                length = math.hypot(d[0], d[2])
                if length < 0.08:
                    raise ValueError('スマホを画面の裏側（撮影方向）へ水平に10〜30cm動かしてから押してください。')
                if abs(d[1]) > max(0.1, length * 0.5):
                    raise ValueError('上下ではなく、撮影方向へ水平に動かしてください。')
                physical_yaw = math.degrees(math.atan2(d[0], d[2]))
                self.space_yaw = a['camera'][4] - physical_yaw
                a['space'] = rotation([0, self.space_yaw, 0])
                a['mount'] = mm(mm(transpose(a['rotation']), transpose(a['space'])), rotation(a['camera'][3:]))
                self.heading_ready = True
            elif action == 'follow':
                enabled = bool(data['enabled'])
                if enabled and (self.anchor is None or self.tracked is None or not self.heading_ready):
                    raise ValueError('基準合わせ後、スマホを前へ水平に10〜30cm動かして「前方向を記録」を押してください。')
                if enabled:
                    self.send('/usercamera/LookAtMe', [False])
                    self.send('/usercamera/AutoLevelRoll', [False])
                    self.send('/usercamera/AutoLevelPitch', [False])
                    self.send('/usercamera/SmoothMovement', [False])
                self.follow = enabled
            elif action == 'capture':
                self.send('/usercamera/Capture', [False])
                self.send('/usercamera/Capture', [True])
                self.send('/usercamera/Capture', [False])
            elif action == 'zoom':
                value = float(data['value'])
                if not math.isfinite(value) or not 20 <= value <= 150:
                    raise ValueError('Zoomは20〜150です。')
                self.send('/usercamera/Zoom', [value])
            elif action == 'mode':
                value = int(data['value'])
                if value not in (1, 2):
                    raise ValueError('Photo/Streamを選択してください。')
                self.send('/usercamera/Mode', [value])
            elif action == 'video':
                index = data.get('index')
                if index is not None and (type(index) is not int or not 0 <= index <= 20):
                    raise ValueError('映像番号は0〜20です。')
                self.video_index = index
                self.video_source = 'camera' if index is not None else 'off'
                self.video_generation += 1
                self.frame = None
                self.frame_at = 0
                self.video_error = '映像接続中…' if index is not None else '映像OFF'
            elif action == 'spout':
                name = str(data.get('sender', ''))
                if len(name) > 256 or '\x00' in name:
                    raise ValueError('Spout出力名が不正です。')
                self.spout_name = name
                self.video_source = 'spout'
                self.video_generation += 1
                self.frame = None
                self.frame_at = 0
                self.video_error = 'Spout接続中…'
                self.send('/usercamera/Mode', [2])
                self.send('/usercamera/Streaming', [True])
            else:
                raise ValueError('Unknown action')


def handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log the access token.

        def respond(self, code, body, mime='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return secrets.compare_digest(parse_qs(urlparse(self.path).query).get('token', [''])[0], bridge.token)

        def do_GET(self):
            if not self.authorized():
                self.respond(403, b'{"error":"Use the URL printed in the PC terminal."}')
                return
            path = urlparse(self.path).path
            if path == '/':
                self.respond(200, Path(__file__).with_name('index.html').read_bytes(), 'text/html; charset=utf-8')
            elif path == '/api/status':
                self.respond(200, json.dumps(bridge.status(), ensure_ascii=False).encode())
            elif path == '/video':
                self.send_response(200)
                self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                try:
                    last = 0
                    while not bridge.stop.wait(1 / 24):
                        with bridge.lock:
                            frame, at = bridge.frame, bridge.frame_at
                            active = bridge.video_source != 'off'
                        if not active:
                            break
                        if frame and at != last and time.monotonic() - at < 2:
                            last = at
                            self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                                             str(len(frame)).encode() + b'\r\n\r\n' + frame + b'\r\n')
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
            else:
                self.respond(404, b'{}')

        def do_POST(self):
            if not self.authorized() or urlparse(self.path).path != '/api/action':
                self.respond(403, b'{}')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size < 8192:
                    raise ValueError('Invalid request size')
                data = json.loads(self.rfile.read(size))
                bridge.action(data)
                self.respond(200, b'{"ok":true}')
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.respond(400, json.dumps({'error': str(exc)}, ensure_ascii=False).encode())
    return Handler


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--osc-host', default='127.0.0.1')
    parser.add_argument('--osc-port', type=int, default=9000)
    parser.add_argument('--receive-port', type=int, default=9001)
    parser.add_argument('--demo', action='store_true', help='Synthetic tracker; sends NO OSC')
    parser.add_argument('--token', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--tracker', default='', help='Preselect tracker serial')
    args = parser.parse_args()
    bridge = Bridge(args)
    if args.token:
        bridge.token = args.token
    bridge.selected = args.tracker
    server = ThreadingHTTPServer((args.host, args.port), handler(bridge))
    server.daemon_threads = True
    targets = (bridge.tracking, bridge.video) if args.demo else (bridge.tracking, bridge.video, bridge.receive)
    for target in targets:
        threading.Thread(target=target, daemon=True).start()
    print('DEMO — OSC送信なし' if args.demo else 'VIVE Tracker → VRChat OSC', flush=True)
    print(f'PC: http://127.0.0.1:{args.port}/?token={bridge.token}', flush=True)
    for address in sorted(set(socket.gethostbyname_ex(socket.gethostname())[2])):
        if not address.startswith('127.'):
            print(f'iPhone (同じWi-Fi): http://{address}:{args.port}/?token={bridge.token}', flush=True)
    print('終了: Ctrl+C。VRChatのカメラUIを開いてOSCを有効にしてください。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.follow = False
        bridge.stop.set()
        server.server_close()
        bridge.sender.close()


if __name__ == '__main__':
    main()
