# VRC Phone Camera — 試作

VIVE Tracker付きiPhoneを、PC版VRChatの手持ちカメラとして使うための橋渡しソフト。
Safariの操作画面でトラッカー選択、基準合わせ、追従、撮影、画角変更、Spout映像表示ができます。本体の音量ボタンも使う場合は付属のiOSアプリを使います（MacのXcodeでビルド・実機へのインストールが必要）。

## 起動

1. PCでSteamVRを起動し、スマホに固定したVIVE Trackerが追跡されている状態にします。
2. VRChatのアクションメニュー → OSC → EnabledをON。VRChatのカメラUIを開いたままにします。
3. `start.cmd` をダブルクリック。初回はPythonライブラリをインストールします（Python 3.12/3.13とインターネット接続が必要）。このPCではCodex付属のPython 3.12環境を使えます。
4. ターミナルに表示される「iPhone (同じWi-Fi)」のURLをiPhoneのSafariで開きます。接続できない場合はWindowsファイアウォールのプライベートネットワークでPythonを許可し、PCとiPhoneが同じネットワークであることを確認します。VPN等の複数アドレスが表示された場合はWi-Fi/LANのアドレスを使います。
5. スマホに付けたトラッカーのシリアルを選び「選択」。身体用トラッカーと区別してください。
6. VRChat内でカメラを動かしてOSC姿勢を受信させます。仮想カメラを撮影開始位置に置きます。スマホを横向き、画面を手前、トラッカーを上にして水平に持ち「① 基準合わせ」。仮想カメラの傾きは水平にリセットされます。5秒以上古い受信値では基準合わせできません。
7. 同じ向きを保ち、スマホを画面の裏側（撮影方向）へ水平に10〜30cm動かして「② 前方向を記録」。この移動から実空間と仮想空間の水平の向きの差を計算します。上へ動かしたり、左右に動かしたりせず、撮影方向へ押し出してください。
8. スマホを基準位置へ戻して「③ 追従開始」。上下移動は仮想空間の上下移動になります。平行移動だけではカメラの角度は変わりません。必要に応じて停止して基準合わせと前方向の記録をやり直してください。

追跡を失った場合は自動停止します。追跡が戻ったら、位置を確認して追従開始してください。
基準合わせは保存しません。起動時は停止状態です。追従開始時にLook At Me、Roll/Pitch自動水平補正、Smooth MovementをOFFにします。
撮影した写真はVRChatの通常のPC保存先に保存されます。

## iPhoneへの映像

SpoutGLでVRChatのSpout映像を直接読み取り、MJPEGでSafari／iOSアプリへ転送します。OBSは不要です。

1. VRChatのカメラUIを開き、StreamモードとSpout StreamをON。
2. 画面のSpout出力欄でVRChatのカメラ出力を選択し「Spout映像ON」。自動選択はVRCSender系／名前にVRChatを含む出力を選びます。
3. 「Spout映像ON」はOSCでStreamモードとSpout StreamのONも指示します。反映されない場合はVRChat内のUIで手動設定してください。
4. 停止は「映像OFF」。別デバイスを使う場合は折りたたみ欄からOBS Virtual Cameraなどを選べます。

映像は最大約24fpsを目標とするJPEG連続転送、横幅最大1280px、音声なし。回線・読み取り・エンコードで遅延が発生します。映像デバイスは明示的にONにするまで開きません。

## 画面ボタンと本体音量ボタン

画面の「撮影」はSafari／アプリの両方で使えます。本体の音量↑・↓は付属の `ios/VRCPhoneCamera.xcodeproj` アプリで使います。手順・制約は `ios/README.md` を参照してください。
本体ボタンのAPIは実カメラが動作する前景アプリでのみ使えるため、ON中は実カメラを小窓で表示します。実カメラ映像は保存・送信しません。
Windowsでは直接ビルドできないため、GitHub ActionsのmacOSでビルドします。初回のiPhone向けコンパイルとIPA作成は成功しました。LiveContainer上の実機動作は未検証です。

## 仕様と制限

- 通常動作: OpenVR BackgroundアプリとしてSteamVRのStanding座標系を取得。GenericTrackerのみ表示。
- PC→VRChat: UDP `127.0.0.1:9000`。VRChat→PC: UDP `127.0.0.1:9001`。他アプリが9001を占有していると自動基準合わせできません。
- 位置・回転: `/usercamera/Pose` にfloat 6個（XYZ位置、XYZ Euler角）を約60Hzで送信。
- OpenVRの右手系をUnity左手系へ変換し、ZXY Euler順を使用。位置の座標変換は水平の向き（Yaw）のみで、高さYを保ちます。取り付け補正は回転の右側に適用します（Rcamera = Rspace × Rtracker × Rmount）。旧版では取り付け補正と空間の向きを混同していたため、上下と前後が入れ替わる不具合がありました。
- 画面にOpenVRから取得したXYZ・角度、基準位置からの移動量、カメラへ出力するXYZ・角度を表示します。位置追跡と回転を区別して確認できます。
- スマホとトラッカーは動かないよう固定してください。この試作は基準時の向きのずれを補正しますが、トラッカーからスマホの仮想レンズまでの物理的な距離の補正は未実装です。
- ワールド移動、プレイスペース変更、VR内の移動・旋回・アバター倍率変更後は停止して基準合わせをやり直してください。VRChat内のプレイヤー移動への自動追従補正は未実装。
- OSCのカメラPose制御は公式2025.3.3リリースノートで取得/設定可能と説明されていますが、公式Wikiには読み取り専用との食い違いがあります。カメラUIを閉じるとOSC操作できないという報告もあります。まずUIを開いたPhoto/Streamで試してください。Droneは対象外です。
- サーバーはLANへ公開されます。起動ごとに発行するランダムURLトークンで操作と映像へのアクセスを制限します。URLは信頼できる端末でのみ使用し、インターネット向けポート転送は不要です。終了はPCターミナルでCtrl+C。
- スマホブラウザを閉じてもPC側で追従は続きます。終了前に停止、またはPCでCtrl+Cしてください。

## ハードウェアなしで画面を試す

`start.cmd -Demo` または `python app.py --demo`。
仮想トラッカーが動き、操作画面と出力計算を試せます。**DemoではVRChatへのOSC送信を行いません。**

## コマンド設定

`python app.py --help` でHTTPポート、OSC送信先・受信ポートを変更できます。
HTTP既定ポート: 8765。SteamVR実機からトラッカーの検出・有効な姿勢の取得を確認済みです。VRChatのカメラ連動とiPhone上での実動作は未検証です。

## 一覧が空の場合

「再検出」でOpenVR接続を作り直せます。起動後にトラッカーが見つからない場合も5秒ごとに再接続します。
画面上部にSteamVRの接続デバイス数とトラッカー数を表示します。1台の場合は選択欄にそのシリアルが表示されるので「選択」を押してください。
デモはOSC受信ポートを開きません。以前の版のデモが動いたままならCtrl+Cで終了してください。

## 確認した一次資料

- VRChatカメラOSC: https://docs.vrchat.com/docs/vrchat-202533
- OSCポートと有効化: https://docs.vrchat.com/docs/osc-overview
- Spout/Windows PCVR: https://docs.vrchat.com/docs/vrchat-202433-openbeta
- OpenVR Tracking API: https://github.com/ValveSoftware/openvr/wiki/IVRSystem%3A%3AGetDeviceToAbsoluteTrackingPose
- Python OpenVR bindings: https://github.com/cmbruns/pyopenvr
- Python SpoutGL: https://github.com/jlai/Python-SpoutGL
- Apple本体ボタンAPI: https://developer.apple.com/documentation/avkit/avcaptureeventinteraction
