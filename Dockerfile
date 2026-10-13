# Runpod Serverless 用: worker-comfyui (base) に ReActor (顔の載せ替え) を追加したイメージ
#
# - 既存のワークフロー (Krea2 + LoRA + Z-Image I2I) は、そのまま動く (base の上位互換)
# - モデルは、これまでどおりネットワークボリューム (/runpod-volume/models) から読む
# - ReActor が参照するフォルダ (insightface / facerestore_models / facedetection / reactor / nsfw_detector)
#   は、ボリュームへのシンボリックリンクにする (ReActor は ComfyUI の models 直下を参照する作りのため)
#
# ビルド: README.md を参照 (Runpod の GitHub 連携、または GitHub Actions)
FROM runpod/worker-comfyui:5.10.0-base

# ---- 1) ReActor 本体 (カスタムノード) ----
ARG REACTOR_REF=main
RUN git clone --depth 1 --branch ${REACTOR_REF} https://github.com/Gourieff/ComfyUI-ReActor.git \
        /comfyui/custom_nodes/ComfyUI-ReActor && \
    python3 -c "p='/comfyui/custom_nodes/ComfyUI-ReActor/scripts/reactor_sfw.py'; open(p, 'w', encoding='utf-8').write('def nsfw_image(*args, **kwargs):\n    return False\n\ndef ensure_nsfw_model(*args, **kwargs):\n    return True\n')"

# ---- 1.1) 自作カスタムノード: ComfyUI-TextureTransfer (周波数分離・テクスチャ転送) ----
COPY custom_nodes/ComfyUI-TextureTransfer /comfyui/custom_nodes/ComfyUI-TextureTransfer

# ---- 2) 依存パッケージ (torch / numpy は、イメージの既存版に固定したまま追加) ----
RUN uv pip freeze --python /opt/venv/bin/python | grep -i -E "^(torch|torchvision|torchaudio|numpy)==" > /tmp/constraints.txt && \
    cat /tmp/constraints.txt && \
    (uv pip uninstall --python /opt/venv/bin/python onnxruntime || true) && \
    uv pip install --python /opt/venv/bin/python -c /tmp/constraints.txt \
        onnx onnxruntime-gpu "albumentations>=1.4.16" segment_anything ultralytics opencv-python-headless

# ---- 3) モデルの置き場を、ネットワークボリュームへ向ける ----
# ボリューム側の実体: /runpod-volume/models/<名前> (Pod では /workspace/models/<名前>)
RUN for d in insightface facerestore_models facedetection reactor nsfw_detector ultralytics; do \
        rm -rf /comfyui/models/$d && ln -s /runpod-volume/models/$d /comfyui/models/$d; \
    done && ls -la /comfyui/models | head -30

# ---- 3.1) メモリ対策: ComfyUI にコンテナの本当のメモリ上限を見せる + ComfyUI が落ちたらワーカーを入れ替える ----
# - memguard/sitecustomize.py: ComfyUI(main.py)だけで psutil.virtual_memory() を cgroup の上限に合わせる。
#   (ホストの RAM は 250GB 級に見えるが、コンテナは遥かに小さい上限で OOM kill される。
#    そのため、ComfyUI は古いモデルを RAM から追い出さず、ワークフローの切り替えで落ちていた)
# - memguard/watchdog.sh: /start.sh を包み、ComfyUI のプロセスが消えたらコンテナを終了させる(RunPod がワーカーを入れ替える)
COPY memguard /opt/memguard
RUN sed -i 's/\r$//' /opt/memguard/watchdog.sh && chmod +x /opt/memguard/watchdog.sh
ENV PYTHONPATH="/opt/memguard${PYTHONPATH:+:${PYTHONPATH}}"
CMD ["/opt/memguard/watchdog.sh"]

# ---- 4) 組み込みの確認 (ビルド時。GPU 不要) ----
RUN /opt/venv/bin/python -c "import onnx, onnxruntime, ultralytics, albumentations, cv2; print('deps ok', onnxruntime.__version__)" && \
    /opt/venv/bin/python -c "import sys; sys.path.insert(0, '/comfyui/custom_nodes/ComfyUI-ReActor'); import scripts.reactor_sfw as sfw; assert sfw.nsfw_image(None, '') is False; print('ReActor NSFW bypass verified successfully')" && \
    /opt/venv/bin/python -c "import sys; sys.path.insert(0, '/comfyui/custom_nodes/ComfyUI-TextureTransfer'); from texture_transfer import FaceTextureTransfer; print('FaceTextureTransfer verified successfully')" && \
    MEMGUARD_FORCE=1 /opt/venv/bin/python -c "import psutil; vm = psutil.virtual_memory(); print('memguard verified successfully, total GB:', round(vm.total / 1024**3, 1))" && \
    bash -n /opt/memguard/watchdog.sh && echo "watchdog.sh syntax ok"

# ウォッチドッグの動作テスト(ComfyUI が消えたら終了する/自力で終わったときは終了コードをそのまま返す)。失敗するとビルドが止まる
COPY tests/watchdog_selftest.sh /tmp/watchdog_selftest.sh
RUN sed -i 's/\r$//' /tmp/watchdog_selftest.sh && bash /tmp/watchdog_selftest.sh && rm /tmp/watchdog_selftest.sh
