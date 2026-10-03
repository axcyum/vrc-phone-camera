import UIKit
import WebKit
import AVFoundation
import AVKit

@main
class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = CameraController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}

final class CameraController: UIViewController, WKNavigationDelegate, UITextFieldDelegate {
    private let web = WKWebView(frame: .zero)
    private let address = UITextField()
    private let message = UILabel()
    private let hardware = UIButton(type: .system)
    private let preview = UIView()
    private let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "vrc-phone-camera.session")
    private var previewLayer: AVCaptureVideoPreviewLayer?
    private var interaction: AVCaptureEventInteraction?
    private var endpoint: URL?
    private var configured = false
    private var wantsHardware = false
    private var inFlight = false
    private var lastCapture = Date.distantPast

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        address.borderStyle = .roundedRect
        address.placeholder = "PCのiPhone用URLを貼り付け"
        address.text = UserDefaults.standard.string(forKey: "pcURL")
        address.autocapitalizationType = .none
        address.autocorrectionType = .no
        address.keyboardType = .URL
        address.returnKeyType = .go
        address.delegate = self
        let connect = UIButton(type: .system)
        connect.setTitle("接続", for: .normal)
        connect.addTarget(self, action: #selector(connectPC), for: .touchUpInside)
        let top = UIStackView(arrangedSubviews: [address, connect])
        top.spacing = 8
        connect.setContentHuggingPriority(.required, for: .horizontal)
        hardware.setTitle("音量ボタンを有効にする", for: .normal)
        hardware.addTarget(self, action: #selector(toggleHardware), for: .touchUpInside)
        message.text = "画面の撮影ボタンはカメラ許可なしで使えます。"
        message.textColor = .lightGray
        message.font = .systemFont(ofSize: 12)
        message.numberOfLines = 2
        let info = UIStackView(arrangedSubviews: [hardware, message])
        info.axis = .vertical
        info.alignment = .leading
        let layout = UIStackView(arrangedSubviews: [top, info, web])
        layout.axis = .vertical
        layout.spacing = 6
        layout.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(layout)
        NSLayoutConstraint.activate([
            layout.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 8),
            layout.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -8),
            layout.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            layout.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor),
            top.heightAnchor.constraint(equalToConstant: 42)
        ])
        web.navigationDelegate = self
        web.isOpaque = false
        web.backgroundColor = .black
        preview.backgroundColor = .darkGray
        preview.layer.cornerRadius = 8
        preview.clipsToBounds = true
        preview.isHidden = true
        preview.isUserInteractionEnabled = false
        preview.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(preview)
        NSLayoutConstraint.activate([
            preview.widthAnchor.constraint(equalToConstant: 84),
            preview.heightAnchor.constraint(equalToConstant: 64),
            preview.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -12),
            preview.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12)
        ])
        let events = AVCaptureEventInteraction { [weak self] event in
            if event.phase == .ended { self?.captureRemote() }
        }
        events.isEnabled = false
        view.addInteraction(events)
        interaction = events
        NotificationCenter.default.addObserver(self, selector: #selector(background), name: UIApplication.didEnterBackgroundNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(foreground), name: UIApplication.didBecomeActiveNotification, object: nil)
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted), name: .AVCaptureSessionWasInterrupted, object: session)
        NotificationCenter.default.addObserver(self, selector: #selector(interrupted), name: .AVCaptureSessionRuntimeError, object: session)
    }

    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); previewLayer?.frame = preview.bounds }
    func textFieldShouldReturn(_ textField: UITextField) -> Bool { connectPC(); return true }

    private func localHost(_ host: String) -> Bool {
        if host == "localhost" || host == "::1" || host.hasSuffix(".local") { return true }
        let segments = host.split(separator: ".")
        guard segments.count == 4 else { return false }
        let p = segments.compactMap { Int($0) }
        guard p.count == 4, p.allSatisfy({ (0...255).contains($0) }) else { return false }
        return p[0] == 10 || p[0] == 127 || (p[0] == 192 && p[1] == 168) ||
            (p[0] == 172 && (16...31).contains(p[1])) || (p[0] == 169 && p[1] == 254)
    }
    @objc private func connectPC() {
        guard let text = address.text?.trimmingCharacters(in: .whitespacesAndNewlines),
              let url = URL(string: text), let host = url.host,
              ["http", "https"].contains(url.scheme ?? ""), localHost(host),
              let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.queryItems?.contains(where: { $0.name == "token" && !($0.value ?? "").isEmpty }) == true else {
            message.text = "同じWi-FiのPCのURL（token付き）を貼り付けてください。"; return
        }
        endpoint = url
        UserDefaults.standard.set(text, forKey: "pcURL")
        address.resignFirstResponder()
        web.load(URLRequest(url: url))
        message.text = "接続中…"
        UIApplication.shared.isIdleTimerDisabled = true
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        message.text = "画面の撮影ボタンが使えます。本体ボタンは上のスイッチで有効にします。"
    }
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let base = endpoint,
              url.host == base.host, url.port == base.port, url.scheme == base.scheme else { decisionHandler(.cancel); return }
        decisionHandler(.allow)
    }
    @objc private func toggleHardware() {
        if wantsHardware {
            wantsHardware = false; stopCamera()
            hardware.setTitle("音量ボタンを有効にする", for: .normal)
            message.text = "本体ボタンOFF。通常の音量操作に戻ります。"; return
        }
        guard endpoint != nil else { message.text = "先にPCへ接続してください。"; return }
        AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
            DispatchQueue.main.async {
                guard let self = self else { return }
                guard granted else { self.message.text = "カメラ許可が必要です。画面の撮影ボタンは使えます。"; return }
                self.wantsHardware = true; self.startCamera()
            }
        }
    }
    private func startCamera() {
        queue.async { [weak self] in
            guard let self = self else { return }
            do {
                if !self.configured {
                    guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) else { throw CameraError.unavailable }
                    let input = try AVCaptureDeviceInput(device: device)
                    let output = AVCapturePhotoOutput()
                    self.session.beginConfiguration()
                    self.session.sessionPreset = .low
                    guard self.session.canAddInput(input), self.session.canAddOutput(output) else {
                        self.session.commitConfiguration(); throw CameraError.unavailable
                    }
                    self.session.addInput(input); self.session.addOutput(output)
                    self.session.commitConfiguration(); self.configured = true
                }
                self.session.startRunning()
                let running = self.session.isRunning
                DispatchQueue.main.async {
                    guard self.wantsHardware, UIApplication.shared.applicationState == .active else { self.stopCamera(); return }
                    guard running else { self.interrupted(); return }
                    if self.previewLayer == nil {
                        let layer = AVCaptureVideoPreviewLayer(session: self.session)
                        layer.videoGravity = .resizeAspectFill
                        layer.frame = self.preview.bounds
                        self.preview.layer.addSublayer(layer); self.previewLayer = layer
                    }
                    self.preview.isHidden = false; self.interaction?.isEnabled = true
                    self.hardware.setTitle("音量ボタンを無効にする", for: .normal)
                    self.message.text = "音量↑／↓でVRChatを撮影。実カメラは小窓のみ、保存・送信しません。"
                }
            } catch {
                DispatchQueue.main.async { self.wantsHardware = false; self.message.text = "実カメラを起動できません。画面の撮影ボタンを使ってください。" }
            }
        }
    }
    private func stopCamera() {
        interaction?.isEnabled = false; preview.isHidden = true
        queue.async { [weak self] in self?.session.stopRunning() }
    }
    @objc private func background() { stopCamera(); UIApplication.shared.isIdleTimerDisabled = false }
    @objc private func foreground() {
        if endpoint != nil { UIApplication.shared.isIdleTimerDisabled = true }
        if wantsHardware { startCamera() }
    }
    @objc private func interrupted() {
        interaction?.isEnabled = false
        message.text = "実カメラが中断されました。本体ボタンをOFFにして再度ONにしてください。"
    }
    private func captureRemote() {
        guard wantsHardware, interaction?.isEnabled == true, !inFlight,
              Date().timeIntervalSince(lastCapture) > 0.3, let base = endpoint,
              var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return }
        parts.path = "/api/action"; parts.fragment = nil
        guard let url = parts.url else { return }
        inFlight = true; lastCapture = Date()
        var request = URLRequest(url: url)
        request.httpMethod = "POST"; request.timeoutInterval = 3
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("{\"action\":\"capture\"}".utf8)
        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.inFlight = false
                if error == nil, (response as? HTTPURLResponse)?.statusCode == 200 {
                    UIImpactFeedbackGenerator(style: .light).impactOccurred()
                    self.message.text = "撮影指示を送信しました（写真はPCへ保存）。"
                } else { self.message.text = "撮影指示の送信に失敗しました。PC接続を確認してください。" }
            }
        }.resume()
    }
    private enum CameraError: Error { case unavailable }
}
