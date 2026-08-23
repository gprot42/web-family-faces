"""Local-first family photo identity."""

from . import thread_limits  # noqa: F401  # cap BLAS/ONNX before sidecar imports

from . import sidecar

__version__ = "0.1.0"

__all__ = ["sidecar"]
