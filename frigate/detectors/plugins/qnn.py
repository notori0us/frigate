import atexit
import logging
import os
import re
from typing import Literal

import cv2
import numpy as np
from pydantic import ConfigDict, Field
from typing_extensions import Annotated

from frigate.detectors.detection_api import DetectionApi
from frigate.detectors.detector_config import BaseDetectorConfig, ModelTypeEnum

try:
    from qai_appbuilder import (
        LogLevel,
        PerfProfile,
        ProfilingLevel,
        QNNConfig,
        QNNContext,
        Runtime,
    )

    QNN_SUPPORT = True
except ImportError:
    QNN_SUPPORT = False

logger = logging.getLogger(__name__)

DETECTOR_KEY = "qnn"
DEFAULT_QNN_LIB_DIR = "/opt/qairt/lib"
MAX_DETECTIONS = 20

# QAIRT version this image's qai_appbuilder was built against (baked by the
# Dockerfile from QAIRT_SDK_VERSION). The host-mounted QAIRT runtime must match
# this minor version or QNN init fails silently (transport error 4000).
BUILD_QAIRT_VERSION = os.environ.get("FRIGATE_QNN_BUILD_QAIRT_VERSION", "")


def _runtime_qairt_version(lib_dir: str) -> str | None:
    """Best-effort host QAIRT version, read from the mounted libQnnHtp.so (it
    embeds e.g. ``v2.38.0.250901140452``). Returns ``major.minor.patch.date`` or
    None if it can't be determined."""
    try:
        with open(os.path.join(lib_dir, "libQnnHtp.so"), "rb") as f:
            blob = f.read()
    except OSError:
        return None
    m = re.search(rb"(\d+\.\d+\.\d+)\.(\d{6})\d*", blob)
    return f"{m.group(1).decode()}.{m.group(2).decode()}" if m else None


def _qairt_mismatch(build: str, runtime: str | None) -> bool:
    """True only when both versions are known and differ in major.minor.patch
    (the ABI-relevant part). The build-date suffix is ignored."""
    if not build or not runtime:
        return False
    return runtime.split(".")[:3] != build.split(".")[:3]


def _release(ctx) -> None:
    """Best-effort QNNContext release. The bindings don't expose a public
    ``close()``; relying on Python GC is fine in normal exit, but registering
    this as an atexit handler ensures it happens before interpreter shutdown
    starts tearing down C++ globals — which is what previously left fastrpc
    fds dangling on detector-subprocess restart."""
    try:
        del ctx
    except Exception:
        pass


class QnnDetectorConfig(BaseDetectorConfig):
    """QNN detector for Qualcomm Hexagon NPUs via QAIRT / qai_appbuilder.

    Runs pre-compiled QNN context binaries (.bin) produced by Qualcomm AI Hub
    on the Hexagon NPU. Tested on QCS6490 (Hexagon v68) with YOLOv8 detection.
    """

    model_config = ConfigDict(
        title="QNN",
    )

    type: Literal[DETECTOR_KEY]
    qnn_lib_dir: str = Field(
        default=DEFAULT_QNN_LIB_DIR,
        title="Directory containing QAIRT runtime libraries (libQnnHtp.so etc.).",
    )
    soc_id: str = Field(
        default="6490",
        title="Qualcomm SoC id. Controls output-tensor ordering of the AI Hub "
        "model: '6490' yields [scores, classes, boxes]; other SoCs yield "
        "[boxes, scores, classes].",
    )
    conf_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.25
    iou_threshold: Annotated[float, Field(ge=0.0, le=1.0)] = 0.7


class QnnDetector(DetectionApi):
    type_key = DETECTOR_KEY
    supported_models = [ModelTypeEnum.yologeneric]

    def __init__(self, detector_config: QnnDetectorConfig):
        super().__init__(detector_config)
        if not QNN_SUPPORT:
            logger.error(
                "qai_appbuilder is not installed. Use the -qualcomm Docker "
                "image variant for Qualcomm Hexagon NPU support."
            )
            return

        model_path = detector_config.model.path
        if not model_path or not os.path.exists(model_path):
            raise FileNotFoundError(f"QNN model not found: {model_path}")

        self._input_size = detector_config.model.width
        self._soc_id = detector_config.soc_id
        self._conf = detector_config.conf_threshold
        self._iou = detector_config.iou_threshold
        # _wedged: when set, detect_raw returns zeros without touching the NPU.
        # Used here for a fatal setup error and in detect_raw for a runtime cDSP
        # wedge. We degrade rather than crash: a crashing detector subprocess
        # triggers Frigate's watchdog → restart → stranded fastrpc fds →
        # eventual kernel fault on Linux 6.18.
        self._wedged = False
        self._ctx = None

        # Fail loud on a host/image QAIRT ABI mismatch instead of silently
        # returning zero detections (transport error 4000).
        runtime_qairt = _runtime_qairt_version(detector_config.qnn_lib_dir)
        if _qairt_mismatch(BUILD_QAIRT_VERSION, runtime_qairt):
            logger.error(
                "QAIRT version mismatch: this image's qai_appbuilder was built "
                "against QAIRT %s, but the host runtime mounted at %s is %s — "
                "these are ABI-incompatible and inference would silently return "
                "zero detections. Mount the matching QAIRT %s runtime (see "
                "docs/frigate/installation#qualcomm-platform). Detection "
                "disabled in this detector subprocess.",
                BUILD_QAIRT_VERSION,
                detector_config.qnn_lib_dir,
                runtime_qairt,
                BUILD_QAIRT_VERSION,
            )
            self._wedged = True
            return

        # LogLevel.ERROR (not WARN): QAIRT emits a WARN-level
        # "Time: model_inference yolo Nms" line on every inference, so WARN
        # produces hundreds of thousands of lines/day at 5fps × 7 cameras.
        # ERROR keeps real failures visible without the timing spam.
        # ProfilingLevel.OFF for the same reason.
        QNNConfig.Config(
            detector_config.qnn_lib_dir,
            Runtime.HTP,
            LogLevel.ERROR,
            ProfilingLevel.OFF,
        )
        self._ctx = QNNContext("yolo", model_path)
        PerfProfile.SetPerfProfileGlobal(PerfProfile.BURST)
        # Register clean teardown so the QNNContext (and its underlying
        # fastrpc session) releases on normal interpreter shutdown — this is
        # what keeps the cDSP from accumulating stranded sessions across
        # detector subprocess restarts. Bind to a weak local so the closure
        # can drop the reference without preventing GC.
        ctx_ref = self._ctx
        atexit.register(lambda: setattr(self, "_ctx", None) or _release(ctx_ref))
        logger.info(
            "QNN detector loaded model=%s size=%d soc=%s",
            model_path,
            self._input_size,
            self._soc_id,
        )

    def detect_raw(self, tensor_input: np.ndarray) -> np.ndarray:
        if not QNN_SUPPORT or self._wedged:
            return np.zeros((MAX_DETECTIONS, 6), dtype=np.float32)

        # Frigate hands a view backed by shared-memory mmap. qai_appbuilder's
        # C++ boundary segfaults on non-owning buffers — always copy.
        arr = np.ascontiguousarray(tensor_input, dtype=np.float32)
        if arr.ndim == 3:
            arr = arr[None, ...]
        if arr.size and float(arr.max()) > 1.5:
            arr = arr / 255.0

        outputs = self._ctx.Inference([arr])
        # An empty list (or fewer than 3 outputs) means the underlying cDSP
        # session is unhealthy — typical causes are a stuck remoteproc from
        # a previous detector subprocess crash, or a wheel/QAIRT SDK ABI
        # mismatch on the host. Crashing here triggers Frigate's watchdog
        # to kill+restart this subprocess, which on Linux 6.18 / qcs6490
        # can strand fastrpc fds and ultimately panic the kernel via
        # fastrpc_device_release. Disable the detector locally instead and
        # return zero detections; the user sees "no detections" + the log
        # line below, recovers with a host reboot per the install docs.
        if not isinstance(outputs, list) or len(outputs) < 3:
            self._wedged = True
            logger.error(
                "QNN inference returned unexpected result (type=%s len=%s). "
                "The cDSP session is likely wedged from a prior crash, or "
                "the QAIRT SDK version on the host does not match the "
                "qai_appbuilder build. Disabling detection in this detector "
                "subprocess; reboot the host to recover. See "
                "docs/frigate/installation#qualcomm-platform troubleshooting.",
                type(outputs).__name__,
                len(outputs) if hasattr(outputs, "__len__") else "?",
            )
            return np.zeros((MAX_DETECTIONS, 6), dtype=np.float32)
        return self._decode(outputs)

    def _decode(self, outputs: list[np.ndarray]) -> np.ndarray:
        if self._soc_id == "6490":
            scores = np.asarray(outputs[0]).reshape(-1)
            classes = np.asarray(outputs[1]).reshape(-1).astype(np.int32)
            boxes = np.asarray(outputs[2]).reshape(-1, 4)
        else:
            boxes = np.asarray(outputs[0]).reshape(-1, 4)
            scores = np.asarray(outputs[1]).reshape(-1)
            classes = np.asarray(outputs[2]).reshape(-1).astype(np.int32)

        mask = scores >= self._conf
        boxes, scores, classes = boxes[mask], scores[mask], classes[mask]

        out = np.zeros((MAX_DETECTIONS, 6), dtype=np.float32)
        if boxes.size == 0:
            return out

        cv_boxes = np.stack(
            [
                boxes[:, 0],
                boxes[:, 1],
                boxes[:, 2] - boxes[:, 0],
                boxes[:, 3] - boxes[:, 1],
            ],
            axis=1,
        ).tolist()
        idxs = cv2.dnn.NMSBoxes(cv_boxes, scores.tolist(), self._conf, self._iou)
        if len(idxs) == 0:
            return out
        idxs = np.asarray(idxs).reshape(-1)[:MAX_DETECTIONS]

        size = float(self._input_size)
        for slot, i in enumerate(idxs):
            x1, y1, x2, y2 = boxes[i]
            out[slot] = (
                float(classes[i]),
                float(scores[i]),
                float(np.clip(y1 / size, 0.0, 1.0)),
                float(np.clip(x1 / size, 0.0, 1.0)),
                float(np.clip(y2 / size, 0.0, 1.0)),
                float(np.clip(x2 / size, 0.0, 1.0)),
            )
        return out


def doctor() -> int:
    """Preflight self-check for the Qualcomm Hexagon runtime. Run it inside the
    container::

        docker exec <frigate> python3 -m frigate.detectors.plugins.qnn

    Prints PASS/FAIL for each prerequisite and exits non-zero if a required
    piece is missing — turns the otherwise-silent setup failures (wrong mount,
    wrong QAIRT version, ``:`` vs ``;`` separator) into a checklist."""
    lib_dir = os.environ.get("QNN_LIB_DIR", DEFAULT_QNN_LIB_DIR)
    ok = True

    def check(label: str, cond: bool, fix: str = "") -> None:
        nonlocal ok
        ok = ok and bool(cond)
        line = f"[{'PASS' if cond else 'FAIL'}] {label}"
        if not cond and fix:
            line += f"\n         fix: {fix}"
        print(line)

    print("== Frigate Qualcomm Hexagon NPU preflight ==")
    print(f"   image built against QAIRT {BUILD_QAIRT_VERSION or '(unknown)'}")
    check("qai_appbuilder importable", QNN_SUPPORT, "use the -qualcomm image variant")
    for dev in (
        "/dev/fastrpc-cdsp",
        "/dev/fastrpc-cdsp-secure",
        "/dev/fastrpc-adsp",
        "/dev/dma_heap/system",
    ):
        check(f"device {dev}", os.path.exists(dev), f"add '{dev}' to compose devices:")
    check(
        "fastrpc device read/write access",
        os.access("/dev/fastrpc-cdsp", os.R_OK | os.W_OK),
        "add group_add with the host's fastrpc GID (getent group fastrpc)",
    )
    check(
        f"QAIRT host libs at {lib_dir}/libQnnHtp.so",
        os.path.exists(os.path.join(lib_dir, "libQnnHtp.so")),
        "mount QAIRT lib/aarch64-oe-linux-gcc11.2 -> /opt/qairt/lib",
    )
    check(
        "Hexagon v68 skel mounted",
        os.path.exists("/opt/qairt/hexagon-v68/unsigned/libQnnHtpV68Skel.so"),
        "mount QAIRT lib/hexagon-v68 -> /opt/qairt/hexagon-v68",
    )
    check(
        "cDSP firmware /usr/lib/dsp/cdsp mounted",
        os.path.isdir("/usr/lib/dsp/cdsp"),
        "-v /usr/lib/dsp:/usr/lib/dsp:ro",
    )
    check(
        "/usr/lib/rfsa/adsp mounted",
        os.path.isdir("/usr/lib/rfsa/adsp"),
        "-v /usr/lib/rfsa:/usr/lib/rfsa:ro",
    )
    check(
        "libcdsprpc.so bundled in image",
        os.path.exists("/usr/lib/libcdsprpc.so"),
        "libcdsprpc.so ships inside the -qualcomm image and is no longer a "
        "host mount; a FAIL means a broken/incomplete image — repull or "
        "rebuild the -qualcomm image",
    )
    check(
        "ADSP_LIBRARY_PATH uses ';' separator",
        ";" in os.environ.get("ADSP_LIBRARY_PATH", ""),
        "use ';' not ':' — libxdsprpc splits ADSP_LIBRARY_PATH on ';'",
    )
    runtime_qairt = _runtime_qairt_version(lib_dir)
    check(
        f"QAIRT version match (host {runtime_qairt or '?'})",
        not _qairt_mismatch(BUILD_QAIRT_VERSION, runtime_qairt),
        f"mount QAIRT {BUILD_QAIRT_VERSION} — host/image minor versions must match",
    )
    print("== " + ("ALL CHECKS PASSED" if ok else "CHECKS FAILED — see fixes above") + " ==")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(doctor())
