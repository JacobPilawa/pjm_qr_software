from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import zxingcpp


class QRDecoder:
    """Decode difficult QR codes with three complementary readers."""

    def __init__(self, model_dir: Path) -> None:
        self.basic = cv2.QRCodeDetector()
        model_paths = [model_dir / name for name in (
            "detect.prototxt", "detect.caffemodel", "sr.prototxt", "sr.caffemodel",
        )]
        missing = [path.name for path in model_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"Missing WeChatQRCode models: {', '.join(missing)}")
        if not hasattr(cv2, "wechat_qrcode_WeChatQRCode"):
            raise RuntimeError("Install opencv-contrib-python 4.12 to enable WeChatQRCode")
        self.wechat = cv2.wechat_qrcode_WeChatQRCode(*(str(path) for path in model_paths))
        self.wechat.setScaleFactor(1.0)

    @property
    def description(self) -> str:
        return "WeChatQRCode + super-resolution -> ZXing-C++ -> OpenCV"

    @staticmethod
    def _item(value: str, corners: Any, decoder: str) -> dict[str, Any]:
        points = np.asarray(corners, dtype=np.float32).reshape(4, 2)
        return {
            "value": value.strip(),
            "corners": points,
            "area": abs(float(cv2.contourArea(points))),
            "decoder": decoder,
        }

    def decode(self, image: np.ndarray) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        try:
            values, points = self.wechat.detectAndDecode(image)
            if points is not None:
                observations.extend(
                    self._item(value, corners, "WeChatQRCode")
                    for value, corners in zip(values, points)
                    if value.strip()
                )
        except cv2.error:
            pass

        if not observations:
            try:
                for barcode in zxingcpp.read_barcodes(
                    image,
                    formats=zxingcpp.BarcodeFormat.QRCode,
                    try_rotate=True,
                    try_downscale=True,
                    try_invert=True,
                ):
                    if not barcode.text.strip():
                        continue
                    position = barcode.position
                    observations.append(self._item(barcode.text, [
                        [position.top_left.x, position.top_left.y],
                        [position.top_right.x, position.top_right.y],
                        [position.bottom_right.x, position.bottom_right.y],
                        [position.bottom_left.x, position.bottom_left.y],
                    ], "ZXing-C++"))
            except (RuntimeError, ValueError):
                pass

        if not observations:
            try:
                ok, values, points, _ = self.basic.detectAndDecodeMulti(image)
            except cv2.error:
                ok, values, points = False, (), None
            if ok and points is not None:
                observations.extend(
                    self._item(value, corners, "OpenCV")
                    for value, corners in zip(values, points)
                    if value.strip()
                )
            if not observations:
                value, corners, _ = self.basic.detectAndDecode(image)
                if value and corners is not None:
                    observations.append(self._item(value, corners, "OpenCV"))

        unique: dict[str, dict[str, Any]] = {}
        for observation in observations:
            value = str(observation["value"])
            current = unique.get(value)
            if current is None or observation["area"] > current["area"]:
                unique[value] = observation
        return list(unique.values())
