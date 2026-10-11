# Serverless へ ReActor を追加する（Krea2＋LoRA＋Z-Image I2I ＋ ReActor ／ Z-Image 単体＋ReActor）

## 全体像
| 要素 | 置き場所 | 状態 |
|---|---|---|
| モデル本体（Z-Image / Krea2 / LoRA / テキストエンコーダ / VAE） | ネットワークボリューム `ixz8wes1x1`（`/runpod-volume/models`） | 既存。変更なし |
| ReActor のモデル（`inswapper_128`、`buffalo_l`、CodeFormer、GFPGAN、顔検出、NSFW検出） | 同上 `models/insightface` `models/facerestore_models` `models/facedetection` `models/nsfw_detector` | **Pod 上で確認済み** |
| **パターン（顔モデル）** | 同上 `models/reactor/faces/*.safetensors`（8個、各6KB） | **Pod 上で作成済み**（`pat_f01〜06`、`pat_mix_f01_f03_f04`、`pat_mix_f02_f05_f06`） |
| ReActor 本体（カスタムノード）と依存パッケージ | **ワーカーのイメージ内** | **イメージのビルドが必要**（この `Dockerfile`） |

> 標準の `worker-comfyui:5.10.0-base` には、ReActor（カスタムノード）も `onnx` も入っていません。
> カスタムノードは、`/run` で渡すワークフローでは追加できないため、**イメージに含める必要があります**。
> （これは、あとから Serverless 側では設定できない部分です。）

## 手順
### 1. イメージをビルドする（いずれか1つ）
**0. 手元の Docker Desktop でビルド → Docker Hub（採用）**
1. Docker Desktop（WSL2）を導入して起動し、`docker login`（Docker Hub の無料アカウント）。
2. `.\build_and_push.ps1 -User <ユーザー名>` を実行（`-NoPush` でビルドのみ）。
3. Runpod のテンプレートのイメージ名に `<ユーザー名>/comfy-reactor-worker:5.10.0-reactor` を指定する。
   - 公開リポジトリなら、Runpod 側のレジストリ認証は不要（イメージにモデルや秘密情報は含まれない）。
   - 非公開にする場合は、Runpod にレジストリ認証（Docker Hub のアクセストークン）を登録する。

**A. Runpod の GitHub 連携（代替）**
1. このフォルダ（`runpod_serverless_reactor/`）の内容を、GitHub のリポジトリ（非公開でよい）に push する。
2. Runpod コンソール → Serverless → **New Endpoint** → **Import Git Repository** で、そのリポジトリを選ぶ（Runpod が `Dockerfile` からビルドする）。
3. ビルドされたイメージが、テンプレートとして登録される。

**B. GitHub Actions → GHCR**
1. リポジトリへ push すると、`.github/workflows/build.yml` が `ghcr.io/<owner>/comfy-reactor-worker:5.10.0-reactor` を公開する。
2. 非公開イメージの場合は、Runpod にレジストリ認証（GitHub の PAT）を登録する。

### 2. 動作確認用の別エンドポイントを作る（既存は変更しない）
- 既存の設定をそのまま使う: GPU（RTX 4090 / A5000 / L4 など）、ネットワークボリューム `ixz8wes1x1`、`workersMax=1`、`idleTimeout=600`、実行タイムアウト 600秒、コンテナディスク 5GB（イメージが増えるため、10GB を推奨）。
- 新しいイメージを指定したテンプレート → エンドポイントを作る。

### 3. 確認（手元から）
```bash
python scripts/runpod_serverless_test.py --endpoint <新ID> --template zimage-reactor --face pat_f04.safetensors
python scripts/runpod_serverless_test.py --endpoint <新ID> --template krea2-zimage-reactor --face pat_f01.safetensors
python scripts/runpod_serverless_test.py --endpoint <新ID> --template krea2-zimage-reactor --off   # 載せ替えなし
```
- 初回はコールドスタート（約150秒＋イメージの取得）。
- 正常なら `runpod_test/serverless_out/` に画像が保存される。

### 4. 本番の切り替え（2026-10-11 実施完了）
- 本番エンドポイント `ehtjs3jnjzn8qh` のテンプレート（`z4y5nsm56h`）のイメージを `ghcr.io/haseyu116-del/comfy-reactor-worker:5.10.0-reactor-nsfw` へ切り替え完了。
- Grok アプリや外部ツール側のエンドポイントID設定（`ehtjs3jnjzn8qh`）やAPI変更は一切不要。
- 新しいイメージは `base` の上位互換のため、既存のワークフロー（ReActorなし）もそのまま稼働。
- **ロールバック手順**: テンプレート `z4y5nsm56h` のイメージを `runpod/worker-comfyui:5.10.0-base`、コンテナディスクを 5GB に戻す。

## テンプレート（`templates/`）
| ファイル | 顔の指定方法 | 内容 |
|---|---|---|
| `krea2-zimage-reactor.json` | **顔モデル**（ボリューム上の `pat_*.safetensors` を名前で指定） | Krea2(DarkBeast＋2step LoRA) → Z-Image(bigLove) I2I → **ReActor** |
| `zimage-reactor.json` | 顔モデル | Z-Image(bigLove) 単体 → **ReActor** |
| `krea2-zimage-reactor-img.json` | **顔画像をリクエストに同送** | 上と同じ（Krea2 → Z-Image I2I → ReActor） |
| `zimage-reactor-img.json` | 顔画像をリクエストに同送 | 上と同じ（Z-Image 単体 → ReActor） |

### 差し替え画像の指定方法（Serverless）
- **顔モデル方式**（`*-reactor.json`）: `face_model` に、ボリューム上のファイル名を指定する。画像の送信は不要で、ペイロードが小さい。よく使うパターン向け。
- **画像方式**（`*-reactor-img.json`）: リクエストの `input.images` に顔画像を base64 で同送し、`face_image`（既定 `face.png`）にその `name` を指定する。**毎回違う顔を使いたいとき**に向く。

```json
{
  "input": {
    "workflow": { "...": "テンプレートの graph (face_image=face.png を指定済み)" },
    "images": [ { "name": "face.png", "image": "<base64>" } ]
  }
}
```
- 顔画像は、**正面の顔がはっきり写った画像**で、1024px 程度に縮小（JPEG 可）を推奨。`/run` のペイロード上限は約10MB。
- 顔画像に複数の顔が写る場合は、`source_faces_index` で選べる（既定は先頭）。
- 動作確認（手元から）:
```bash
python scripts/runpod_serverless_test.py --endpoint <ID> --template zimage-reactor-img --face-image <顔画像のパス>
```

- 形式は既存の `krea2-zimage-twopass.json` と同じ（`description` / `graph` / `params`）。
- 追加した `params`:

| 名前 | 内容 | 既定 |
|---|---|---|
| `face_model` | パターン（顔モデル）のファイル名 | `pat_f01.safetensors` |
| `reactor_enabled` | `false` で載せ替えなし（元の画像を返す） | `true` |
| `face_restore_model` | 顔の補正（`codeformer-v0.1.0.pth` / `GFPGANv1.4.pth` / `GPEN-BFR-512.onnx` / `none`） | `codeformer-v0.1.0.pth` |
| `restore_visibility` | 補正の強さ（0.1〜1） | `1.0` |
| `codeformer_weight` | CodeFormer の重み（0〜1。小さいほど補正が強い） | `0.5` |
| `input_faces_index` | 画像内の何番目の顔を差し替えるか（`0`、`0,1`） | `0` |

## パターン（顔モデル）の追加方法
- Pod を起動し、`runpod_test/build_face_models.py` の方式（`ReActorBuildFaceModel`）で、合成顔から作成する。
- 顔モデルは、**ボリュームに置いたファイルを名前で指定する**だけなので、追加後にワーカー側の設定変更は不要。
- 元にする画像は**合成顔のみ**（収集した実在の画像は使わない。研究方針）。

## 注意（重要）
1. **NSFW 検出と黒画像化の回避（2026-10-11対応）**:
   - 通常版（`5.10.0-reactor`）: ReActorの標準仕様によりNSFW判定された画像が黒画像（512x512）に変換される。
   - **NSFW対応版（`5.10.0-reactor-nsfw`）**: `scripts/reactor_sfw.py` の `nsfw_image()` をパッチし、成人向け・センシティブな画像でも黒画像化せず正常に顔差し替えを実行するよう調整済み。
2. **ライセンス**: `inswapper_128`・InsightFace のモデルは、研究・非商用の条件です。
3. **方針**: 顔の元画像は、実在しない合成顔のみにすること。
4. **検証状況（2026-10-11）**:
   - 通常版: `ghcr.io/haseyu116-del/comfy-reactor-worker:5.10.0-reactor`
   - **NSFW対応版**: `ghcr.io/haseyu116-del/comfy-reactor-worker:5.10.0-reactor-nsfw`（GitHub Actionsでビルド完了・公開済み）
   - テスト用エンドポイント `t7nmfzmjc7h4ac`（テンプレート `bu6ho4khy6`、ボリューム `ixz8wes1x1`、既存の `ehtjs3jnjzn8qh` は未変更）。
5. **再ビルド**: リポジトリ（`comfy-reactor-worker`）の Dockerfile を更新して push すると GitHub Actions が自動起動。
   Docker Desktop は不要（GitHub Actionsクラウド環境でビルド）。リポジトリには秘密情報・ID類を置かないこと（公開リポジトリ）。
