"""Cap BLAS/ONNX thread pools before those libraries load.

buffalo_l otherwise starts a worker pool per model and pegs every core.
"""

from __future__ import annotations

import os

for _key in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "ORT_INTRA_OP_NUM_THREADS",
):
    os.environ.setdefault(_key, "1")

try:
    import cv2

    cv2.setNumThreads(1)
except Exception:
    pass
