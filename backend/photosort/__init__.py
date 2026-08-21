"""Local-first family photo identity."""

import os

# Limit BLAS / OpenMP / ONNX to a couple of cores so matching cannot pin the machine.
for _var, _val in (
    ("OMP_NUM_THREADS", "2"),
    ("OPENBLAS_NUM_THREADS", "1"),
    ("MKL_NUM_THREADS", "1"),
    ("VECLIB_MAXIMUM_THREADS", "1"),
    ("NUMEXPR_NUM_THREADS", "1"),
    ("ONNXRUNTIME_INTRA_OP_NUM_THREADS", "2"),
    ("ORT_INTRA_OP_NUM_THREADS", "2"),
    ("ORT_INTER_OP_NUM_THREADS", "1"),
):
    os.environ[_var] = os.environ.get(_var) or _val

from . import sidecar

__version__ = "0.1.0"

__all__ = ["sidecar"]
