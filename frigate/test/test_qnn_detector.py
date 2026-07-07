import os
import tempfile
import unittest

import numpy as np

from frigate.detectors.plugins import qnn


def _make_detector(soc_id="6490", conf=0.25, iou=0.7, input_size=640):
    """Build a QnnDetector without running __init__ (which needs the QAIRT
    runtime / a real model). Only the attributes _decode reads are set."""
    det = qnn.QnnDetector.__new__(qnn.QnnDetector)
    det._soc_id = soc_id
    det._conf = conf
    det._iou = iou
    det._input_size = input_size
    return det


class TestQnnDecode(unittest.TestCase):
    def test_decode_soc6490_order_scores_classes_boxes(self):
        det = _make_detector(soc_id="6490", input_size=640)
        # 6490 emits outputs as [scores, classes, boxes]; boxes are
        # [x1, y1, x2, y2] in input-pixel space. Three non-overlapping boxes
        # with descending scores survive NMS in input order.
        scores = np.array([0.9, 0.5, 0.4], dtype=np.float32)
        classes = np.array([2, 1, 0], dtype=np.float32)
        boxes = np.array(
            [
                [0, 0, 100, 100],
                [200, 200, 300, 300],
                [400, 400, 500, 500],
            ],
            dtype=np.float32,
        )

        out = det._decode([scores, classes, boxes])

        self.assertEqual(out.shape, (qnn.MAX_DETECTIONS, 6))
        self.assertEqual(out.dtype, np.float32)
        expected = np.zeros((qnn.MAX_DETECTIONS, 6), dtype=np.float32)
        # row layout: [class, score, y1/size, x1/size, y2/size, x2/size]
        expected[0] = [2, 0.9, 0 / 640, 0 / 640, 100 / 640, 100 / 640]
        expected[1] = [1, 0.5, 200 / 640, 200 / 640, 300 / 640, 300 / 640]
        expected[2] = [0, 0.4, 400 / 640, 400 / 640, 500 / 640, 500 / 640]
        np.testing.assert_allclose(out, expected, atol=1e-6)

    def test_decode_non6490_order_boxes_scores_classes_is_equivalent(self):
        # Non-6490 SoCs emit [boxes, scores, classes]; the same detections in
        # the reordered tensor list must decode to the identical array.
        scores = np.array([0.9, 0.5, 0.4], dtype=np.float32)
        classes = np.array([2, 1, 0], dtype=np.float32)
        boxes = np.array(
            [
                [0, 0, 100, 100],
                [200, 200, 300, 300],
                [400, 400, 500, 500],
            ],
            dtype=np.float32,
        )

        out_6490 = _make_detector(soc_id="6490")._decode([scores, classes, boxes])
        out_other = _make_detector(soc_id="8550")._decode([boxes, scores, classes])

        np.testing.assert_array_equal(out_6490, out_other)

    def test_below_conf_threshold_detections_dropped(self):
        det = _make_detector(soc_id="6490", conf=0.25, input_size=640)
        scores = np.array([0.9, 0.1], dtype=np.float32)
        classes = np.array([5, 3], dtype=np.float32)
        boxes = np.array(
            [
                [0, 0, 50, 50],
                [100, 100, 150, 150],
            ],
            dtype=np.float32,
        )

        out = det._decode([scores, classes, boxes])

        # Only the 0.9 detection survives; count filled slots via score column.
        self.assertEqual(np.count_nonzero(out[:, 1]), 1)
        np.testing.assert_allclose(
            out[0], [5, 0.9, 0 / 640, 0 / 640, 50 / 640, 50 / 640], atol=1e-6
        )
        np.testing.assert_array_equal(out[1:], np.zeros((qnn.MAX_DETECTIONS - 1, 6)))

    def test_all_below_threshold_returns_all_zeros(self):
        det = _make_detector(soc_id="6490", conf=0.5)
        scores = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        classes = np.array([1, 2, 3], dtype=np.float32)
        boxes = np.array(
            [
                [0, 0, 10, 10],
                [20, 20, 30, 30],
                [40, 40, 50, 50],
            ],
            dtype=np.float32,
        )

        out = det._decode([scores, classes, boxes])

        self.assertEqual(out.shape, (qnn.MAX_DETECTIONS, 6))
        np.testing.assert_array_equal(
            out, np.zeros((qnn.MAX_DETECTIONS, 6), dtype=np.float32)
        )

    def test_more_than_max_detections_truncated(self):
        det = _make_detector(soc_id="6490", conf=0.25, iou=0.7, input_size=640)
        n = qnn.MAX_DETECTIONS + 5
        # distinct scores all above threshold so NMS keeps ordering stable
        scores = np.linspace(0.9, 0.42, n).astype(np.float32)
        classes = (np.arange(n) % 80).astype(np.float32)
        # non-overlapping 10px boxes on a 5-wide grid (IoU 0 -> none suppressed)
        rows = []
        for i in range(n):
            r, c = divmod(i, 5)
            x1, y1 = c * 20, r * 20
            rows.append([x1, y1, x1 + 10, y1 + 10])
        boxes = np.array(rows, dtype=np.float32)

        out = det._decode([scores, classes, boxes])

        self.assertEqual(out.shape, (qnn.MAX_DETECTIONS, 6))
        # exactly MAX_DETECTIONS slots filled, no more
        self.assertEqual(np.count_nonzero(out[:, 1]), qnn.MAX_DETECTIONS)

    def test_coordinates_clipped_to_unit_range(self):
        det = _make_detector(soc_id="6490", conf=0.25, input_size=640)
        scores = np.array([0.9], dtype=np.float32)
        classes = np.array([1], dtype=np.float32)
        # box extends outside the input frame in both directions
        boxes = np.array([[-50, -50, 700, 700]], dtype=np.float32)

        out = det._decode([scores, classes, boxes])

        coords = out[0, 2:]
        self.assertTrue(np.all(coords >= 0.0))
        self.assertTrue(np.all(coords <= 1.0))
        # y1/x1 clip up to 0, y2/x2 clip down to 1
        np.testing.assert_allclose(out[0], [1, 0.9, 0.0, 0.0, 1.0, 1.0], atol=1e-6)


class TestQairtVersionHelpers(unittest.TestCase):
    def test_mismatch_when_major_minor_patch_differ(self):
        self.assertTrue(qnn._qairt_mismatch("2.38.0.250901", "2.37.0.250801"))

    def test_no_mismatch_when_only_build_date_differs(self):
        self.assertFalse(qnn._qairt_mismatch("2.38.0.250901140452", "2.38.0.250801"))

    def test_no_mismatch_when_version_unknown(self):
        self.assertFalse(qnn._qairt_mismatch("", "2.38.0.250901"))
        self.assertFalse(qnn._qairt_mismatch("2.38.0.250901", None))
        self.assertFalse(qnn._qairt_mismatch("", None))

    def test_runtime_version_missing_dir_returns_none(self):
        self.assertIsNone(qnn._runtime_qairt_version("/nonexistent/does/not/exist"))

    def test_runtime_version_parses_embedded_string(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "libQnnHtp.so"), "wb") as f:
                f.write(b"junk\x00v2.38.0.250901140452 trailing")
            self.assertEqual(qnn._runtime_qairt_version(d), "2.38.0.250901")


if __name__ == "__main__":
    unittest.main()
