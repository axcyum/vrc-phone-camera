# iPhoneアプリ：画面ボタン + 本体音量ボタン

GitHub ActionsでIPAをビルドします。iPhone実機検証は未実施です。

## GitHub Actions → LiveContainer

リポジトリのActionsで `Build iPhone IPA` の成功した実行を開き、Artifactsの `VRCPhoneCamera-LiveContainer` をダウンロード。ZIP内の `VRCPhoneCamera-LiveContainer.ipa` をiPhoneへ移し、LiveContainerの＋から取り込みます。
IPAには開発者署名を付けません。LiveContainer側で必要な署名・起動を行います。通常のiOSインストール用の署名済みIPAではありません。

## Macでビルド

1. Xcodeで `VRCPhoneCamera.xcodeproj` を開きます。
2. Signing & Capabilitiesで自分のTeamを選択し、Bundle Identifierを自分用の一意な値へ変更。
3. iOS 17.2以上の実機iPhoneを接続し、実行先として選んでRun。Xcodeが求める実機側の設定を行ってください。
4. PCで `start.cmd` を実行。表示されたiPhone用のLAN URL（token付き）をアプリへ貼り付け「接続」。
5. 「Spout映像ON」でVRChatカメラ映像を表示。画面の「撮影」でシャッター操作。
6. 「音量ボタンを有効にする」を押し、カメラとローカルネットワークへのアクセスを許可。音量↑・↓のどちらも、離した時にVRChatへ撮影指示を送ります。

写真はPCのVRChatへ保存。本アプリへ自動保存はしません。

## 本体ボタンの制約

Appleの `AVCaptureEventInteraction` を使用します。実カメラのAVCaptureSessionが動作する前景アプリにだけイベントが届くため、本体ボタンON中は前面の実カメラを低解像度の小窓で表示します。実カメラ映像は保存・送信しません。カメラ使用インジケータが表示されます。
OFFで実カメラを停止し、通常の音量操作に戻ります。バックグラウンドでは撮影操作を受けません。カメラ許可を拒否しても、画面ボタンとSpout映像は使えます。
Safari版は画面ボタンのみです。

一次資料: https://developer.apple.com/documentation/avkit/avcaptureeventinteraction
