import json
import shutil
import subprocess
from pathlib import Path

import qrcode
from django.conf import settings
from django.test import SimpleTestCase


class BrowserLogicTests(SimpleTestCase):
    def test_real_qr_decoder_camera_lifecycle_and_idle_privacy(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "浏览器脚本测试需要 Node.js 22 或更新版本")
        payload = "MVP-员工-QR-0001"
        qr = qrcode.QRCode(border=4)
        qr.add_data(payload)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        scale = 5
        pixels = []
        for row in matrix:
            line = []
            for dark in row:
                line.extend(([0, 0, 0, 255] if dark else [255, 255, 255, 255]) * scale)
            pixels.extend(line * scale)
        fixture = {
            "width": len(matrix) * scale,
            "height": len(matrix) * scale,
            "data": pixels,
            "expected": payload,
        }
        result = subprocess.run(
            [node, str(Path(settings.BASE_DIR) / "scripts/test_terminal_ui.cjs")],
            input=json.dumps(fixture),
            text=True,
            capture_output=True,
            timeout=30,
            cwd=settings.BASE_DIR,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)
