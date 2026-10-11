"""ComfyUI-TextureTransfer: Face Texture & Specular Highlight Transfer Node
周波数分離（Frequency Separation）により、ReActorスワップ後の顔に元画像の汗・光沢・フラッシュハイライトを物理合成するノード
"""
import torch
import numpy as np
import cv2

class FaceTextureTransfer:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "base_image": ("IMAGE",),
                "swapped_image": ("IMAGE",),
                "enabled": ("BOOLEAN", {"default": True}),
                "strength": ("FLOAT", {"default": 0.95, "min": 0.0, "max": 2.0, "step": 0.05}),
                "blur_radius": ("INT", {"default": 17, "min": 3, "max": 101, "step": 2}),
                "feather_radius": ("INT", {"default": 45, "min": 5, "max": 151, "step": 2}),
            }
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "apply_texture_transfer"
    CATEGORY = "image/postprocessing"

    def apply_texture_transfer(self, base_image, swapped_image, enabled=True, strength=0.95, blur_radius=17, feather_radius=45):
        # 1. パススルー条件（無効化または強度ゼロ）
        if not enabled or strength <= 0.0:
            return (swapped_image,)

        try:
            device = swapped_image.device
            b_base, h_base, w_base, c_base = base_image.shape
            b_swap, h_swap, w_swap, c_swap = swapped_image.shape

            batch_size = min(b_base, b_swap)
            out_tensors = []

            # 奇数サイズ強制（GaussianBlurのカーネル要件）
            if blur_radius % 2 == 0:
                blur_radius += 1
            if feather_radius % 2 == 0:
                feather_radius += 1

            for i in range(batch_size):
                # ComfyUI Tensor [H, W, C] (float32, 0~1, RGB) -> OpenCV [H, W, C] (uint8, BGR)
                base_np = (base_image[i].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
                swap_np = (swapped_image[i].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)

                base_bgr = cv2.cvtColor(base_np, cv2.COLOR_RGB2BGR)
                swap_bgr = cv2.cvtColor(swap_np, cv2.COLOR_RGB2BGR)

                # 2. 差分解析によるスワップ領域の自動マスク特定
                diff = cv2.absdiff(base_bgr, swap_bgr)
                diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
                _, mask_bin = cv2.threshold(diff_gray, 4, 1.0, cv2.THRESH_BINARY)

                # 差分が極小（スワップされていない等）の場合はそのまま
                if mask_bin.sum() < 50:
                    out_tensors.append(swapped_image[i])
                    continue

                # 3. モルフォロジー処理で顔領域内部の穴埋めと境界拡張
                kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
                mask_closed = cv2.morphologyEx(mask_bin, cv2.MORPH_CLOSE, kernel_close)

                kernel_dilate = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
                mask_dilated = cv2.dilate(mask_closed, kernel_dilate, iterations=1)

                # 4. ガウシアンフェザリング（境界線の極自然なブレンド）
                mask_soft = cv2.GaussianBlur(mask_dilated, (feather_radius, feather_radius), 0)
                mask_3c = np.repeat(mask_soft[:, :, np.newaxis], 3, axis=2)

                # 5. 周波数分離: 元画像から高周波テクスチャ（汗粒・ハイライト反射）を抽出
                base_low = cv2.GaussianBlur(base_bgr, (blur_radius, blur_radius), 0)
                base_high = base_bgr.astype(np.float32) - base_low.astype(np.float32)

                # 6. スワップ画像への高周波合成
                sweat_added = swap_bgr.astype(np.float32) + base_high * float(strength)
                sweat_added = np.clip(sweat_added, 0.0, 255.0)

                # 7. アルファ合成（顔領域のみ適用、背景・身体は100%保護）
                composite_bgr = (swap_bgr.astype(np.float32) * (1.0 - mask_3c) + sweat_added * mask_3c)
                composite_bgr = np.clip(composite_bgr, 0.0, 255.0).astype(np.uint8)

                # OpenCV [H, W, C] (BGR, uint8) -> ComfyUI Tensor [H, W, C] (RGB, float32, 0~1)
                res_rgb = cv2.cvtColor(composite_bgr, cv2.COLOR_BGR2RGB)
                res_tensor = torch.from_numpy(res_rgb.astype(np.float32) / 255.0)
                out_tensors.append(res_tensor)

            result = torch.stack(out_tensors, dim=0).to(device)
            return (result,)

        except Exception as e:
            print(f"[FaceTextureTransfer] Warning: Error during texture transfer: {e}. Falling back to swapped_image.")
            return (swapped_image,)

NODE_CLASS_MAPPINGS = {
    "FaceTextureTransfer": FaceTextureTransfer
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FaceTextureTransfer": "Face Texture & Specular Transfer (周波数分離)"
}
