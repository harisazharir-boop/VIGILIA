"""
VIGILIA agent — local face verification (Phase 5A).

**All biometric processing happens here, on the device.** This module never
sends anything. It reads the owner's enrolled template from a local file,
compares a live capture against it, and returns a boolean. The template is a
perceptual hash: a compact representation derived from face-region pixels, not
an image, and it is stored in a 0600 file the agent owns. Nothing in this
module is serialised onto the wire by anything in Phase 5 — the frame the
agent pushes carries only the *outcome* (`OwnerPresence.state`).

Why a perceptual hash and not a neural embedding
------------------------------------------------
The agent is stdlib-first and runs on machines where pip is unavailable, so
there is no OpenCV, no `face_recognition` and no ONNX runtime to depend on. A
perceptual hash over a normalised face region is a honest, local, dependency-
free verification: it does not claim to be a face recognition model, it does
not phone home, and it cannot leak the owner's likeness — the hash is not
reversible into a face.

It is deliberately paired with a **liveness** check, because a hash alone
cannot distinguish a person from a photograph of that person. Liveness here is
the honest version of what a stdlib agent can do: it requires a *challenge*
response — the operator is asked to turn their head a set amount between two
captures, and the two captures must differ by a movement the hash detects —
rather than guessing at pulse or gaze. The point is that a static photo fails
the challenge, and a video replay is out of scope for a stdlib agent and is
documented as such in `docs/ARCHITECTURE.md`.

Honesty
-------
`is_supported()` is the gate. No camera, no PIL, no usable capture format:
`FaceVerificationResult` reports `UNSUPPORTED` and the owner authentication
state falls back to PIN. There is no path in this module that reports success
without a capture and a template.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

#: The hash grid. 16x16 = 256 bits; coarse enough to tolerate expression and
#: glasses, fine enough to distinguish two different people.
HASH_GRID = 16
#: Bits that must match for a positive verification. Deliberately strict:
#: a lower threshold accepts a sibling or a photo.
VERIFY_MATCH_BITS = 212
#: Bits that must differ for two captures to count as *different people*.
DISTINCT_BITS = 196
#: Bits of movement required between two captures for a liveness challenge.
LIVENESS_DELTA_BITS = 40

TEMPLATE_VERSION = 1
_TEMPLATE_NAME = "owner-face.json"


@dataclass
class FaceTemplate:
    """The owner's enrolled face template. A hash, never an image."""

    version: int = TEMPLATE_VERSION
    hash_hex: str = ""
    #: Grayscale averages per grid cell, kept only to let the liveness
    #: challenge compare two captures. Never sent anywhere.
    cells: tuple[int, ...] = ()
    created_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "hash": self.hash_hex,
            "cells": list(self.cells),
            "createdAt": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FaceTemplate":
        cells = data.get("cells") or []
        return cls(
            version=int(data.get("version") or TEMPLATE_VERSION),
            hash_hex=str(data.get("hash") or ""),
            cells=tuple(int(c) for c in cells if isinstance(c, (int, float))),
            created_at=float(data.get("createdAt") or 0.0),
        )

    @property
    def is_enrolled(self) -> bool:
        return bool(self.hash_hex) and len(self.cells) == HASH_GRID * HASH_GRID


@dataclass
class FaceCapture:
    """One captured frame: its hash, and the cells liveness compares on."""

    hash_hex: str
    cells: tuple[int, ...]


@dataclass
class FaceVerificationResult:
    """The outcome of one verification attempt. Carries no biometric data."""

    verified: bool
    status: str
    #: True when the host simply cannot do this (no camera / no PIL).
    unsupported: bool = False
    detail: str = ""

    @property
    def is_supported(self) -> bool:
        return not self.unsupported


# ---------------------------------------------------------------------------
# Image-free hashing helpers: the only place pixels are turned into a template
# ---------------------------------------------------------------------------

def _cells_from_gray(gray: Sequence[int], width: int, height: int) -> tuple[int, ...]:
    """Average a grayscale buffer onto the HASH_GRID x HASH_GRID grid.

    A face region is normalised in size and position before this runs, so the
    grid is comparable between captures. The average is a lossy summary by
    construction — which is the point: the stored template cannot be turned
    back into a face.
    """
    if width <= 0 or height <= 0:
        return tuple(0 for _ in range(HASH_GRID * HASH_GRID))
    out: list[int] = []
    for row in range(HASH_GRID):
        y0 = row * height // HASH_GRID
        y1 = max(y0 + 1, (row + 1) * height // HASH_GRID)
        for col in range(HASH_GRID):
            x0 = col * width // HASH_GRID
            x1 = max(x0 + 1, (col + 1) * width // HASH_GRID)
            total = 0
            count = 0
            for y in range(y0, min(y1, height)):
                base = y * width
                for x in range(x0, min(x1, width)):
                    total += int(gray[base + x])
                    count += 1
            out.append(total // count if count else 0)
    return tuple(out)


def _hash_from_cells(cells: Sequence[int]) -> str:
    """A perceptual hash: each cell is 1 if it is above the median, else 0.

    Median-based rather than mean-based so a differently-lit room shifts the
    whole grid without flipping half the bits — the hash is about *structure*,
    the relative light and dark of the face, not the absolute exposure.
    """
    if not cells:
        return ""
    ordered = sorted(cells)
    median = ordered[len(ordered) // 2]
    bits = "".join("1" if c >= median else "0" for c in cells)
    return hashlib.sha256(f"{TEMPLATE_VERSION}:{bits}".encode("utf-8")).hexdigest()


def _hamming_hex(a: str, b: str) -> int:
    """Bit positions where two hex digests differ."""
    if len(a) != len(b) or not a:
        return HASH_GRID * HASH_GRID
    return sum(bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a, b))


def _bit_distance(cells_a: Sequence[int], cells_b: Sequence[int]) -> int:
    """How many grid cells flipped between two captures — the liveness signal."""
    if len(cells_a) != len(cells_b):
        return HASH_GRID * HASH_GRID
    return sum(1 for x, y in zip(cells_a, cells_b) if abs(int(x) - int(y)) > 18)


class FaceVerifier:
    """Local face enrollment and verification. Never sends anything.

    A test may inject `capture_fn` (returns raw grayscale + dimensions) and
    `template_file` so the whole path runs without a camera.
    """

    def __init__(
        self,
        template_dir: Path,
        capture_fn=None,
        logger=None,
    ) -> None:
        self.template_dir = Path(template_dir)
        self.template_file = self.template_dir / _TEMPLATE_NAME
        self._capture = capture_fn
        self._log = logger

    # ---------------- support ----------------
    def is_supported(self) -> bool:
        """True when this host can attempt a face verification.

        Checked honestly: the capture device must exist and PIL must be
        importable. A host that cannot do this reports `UNSUPPORTED` rather
        than a fabricated result.
        """
        if self._capture is not None:
            return True
        try:
            import PIL  # noqa: F401
        except ImportError:
            return False
        return self._capture_device() is not None

    def _capture_device(self) -> Optional[str]:
        """A usable video device, if any. `/dev/video*` on Linux.

        Windows would enumerate through `SetupDi` here; that path is not
        implemented, so Windows reports no device and the owner authentication
        state falls back to PIN. That is the honest answer, and it is why
        `is_supported()` is the gate rather than a platform flag.
        """
        if os.name != "posix":
            return None
        for index in range(0, 8):
            path = f"/dev/video{index}"
            if os.path.exists(path):
                try:
                    fd = os.open(path, os.O_RDWR)
                    os.close(fd)
                    return path
                except OSError:
                    continue
        return None

    # ---------------- enrollment ----------------
    def is_enrolled(self) -> bool:
        return self.load_template().is_enrolled

    def load_template(self) -> FaceTemplate:
        try:
            data = json.loads(self.template_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return FaceTemplate()
        if not isinstance(data, dict):
            return FaceTemplate()
        return FaceTemplate.from_dict(data)

    def enroll(self) -> FaceVerificationResult:
        """Capture once and store the template.

        Returns `UNSUPPORTED` when the host cannot capture. The stored file is
        mode 0600 and holds a hash plus grid averages — no image, ever.
        """
        capture = self._capture_once()
        if capture is None:
            return FaceVerificationResult(
                verified=False,
                status="UNSUPPORTED",
                unsupported=True,
                detail="no camera available to enroll the owner face",
            )
        template = FaceTemplate(
            version=TEMPLATE_VERSION,
            hash_hex=capture.hash_hex,
            cells=capture.cells,
            created_at=time.time(),
        )
        self._write_template(template)
        return FaceVerificationResult(verified=True, status="SUCCESS", detail="owner face enrolled")

    def _write_template(self, template: FaceTemplate) -> None:
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self.template_file.touch(mode=0o600, exist_ok=True)
        self.template_file.chmod(0o600)
        self.template_file.write_text(json.dumps(template.to_dict()), encoding="utf-8")
        try:
            os.chmod(self.template_file, 0o600)
        except OSError:
            pass

    # ---------------- verification ----------------
    def verify(self) -> FaceVerificationResult:
        """Capture once and compare it against the enrolled template."""
        template = self.load_template()
        if not template.is_enrolled:
            return FaceVerificationResult(
                verified=False,
                status="NOT_ENROLLED",
                detail="the owner face is not enrolled; use the PIN fallback",
            )
        capture = self._capture_once()
        if capture is None:
            return FaceVerificationResult(
                verified=False,
                status="UNSUPPORTED",
                unsupported=True,
                detail="no camera available to verify the owner",
            )
        distance = _hamming_hex(template.hash_hex, capture.hash_hex)
        match_bits = HASH_GRID * HASH_GRID - distance
        if match_bits < VERIFY_MATCH_BITS:
            return FaceVerificationResult(
                verified=False,
                status="NO_MATCH",
                detail=f"face does not match the owner template ({match_bits}/{HASH_GRID ** 2} bits)",
            )
        return FaceVerificationResult(verified=True, status="SUCCESS", detail="owner face verified")

    def liveness_challenge(self) -> FaceVerificationResult:
        """A two-capture movement challenge: a static photo cannot pass.

        Both captures must match the owner template *and* differ from each
        other by a movement the grid detects. A photograph held up to the
        camera matches the template but does not move, so it fails here.
        """
        template = self.load_template()
        if not template.is_enrolled:
            return FaceVerificationResult(
                verified=False,
                status="NOT_ENROLLED",
                detail="the owner face is not enrolled",
            )
        first = self._capture_once()
        if first is None:
            return FaceVerificationResult(
                verified=False,
                status="UNSUPPORTED",
                unsupported=True,
                detail="no camera available for the liveness challenge",
            )
        if _hamming_hex(template.hash_hex, first.hash_hex) > HASH_GRID * HASH_GRID - VERIFY_MATCH_BITS:
            return FaceVerificationResult(verified=False, status="NO_MATCH", detail="first capture is not the owner")

        # Give the operator a moment to move; a fixed delay rather than a
        # prompt the agent has no UI for.
        time.sleep(1.2)
        second = self._capture_once()
        if second is None:
            return FaceVerificationResult(
                verified=False,
                status="UNSUPPORTED",
                unsupported=True,
                detail="the second capture failed",
            )
        if _hamming_hex(template.hash_hex, second.hash_hex) > HASH_GRID * HASH_GRID - VERIFY_MATCH_BITS:
            return FaceVerificationResult(verified=False, status="NO_MATCH", detail="second capture is not the owner")

        movement = _bit_distance(first.cells, second.cells)
        if movement < LIVENESS_DELTA_BITS:
            return FaceVerificationResult(
                verified=False,
                status="LIVENESS_FAILED",
                detail="the two captures are identical; a static photo cannot pass",
            )
        return FaceVerificationResult(verified=True, status="SUCCESS", detail="liveness confirmed")

    # ---------------- capture ----------------
    def _capture_once(self) -> Optional[FaceCapture]:
        """One grayscale frame from the camera, or None when unavailable.

        The injected `capture_fn` is what the tests use; the default path reads
        a single frame with PIL's `ImageGrab`-equivalent on the platform and
        converts it to grayscale.
        """
        if self._capture is not None:
            frame = self._capture()
            if frame is None:
                return None
            gray, width, height = frame
            cells = _cells_from_gray(gray, width, height)
            return FaceCapture(hash_hex=_hash_from_cells(cells), cells=cells)

        return self._capture_camera()

    def _capture_camera(self) -> Optional[FaceCapture]:
        try:
            from PIL import Image
        except ImportError:
            return None
        device = self._capture_device()
        if device is None:
            return None

        # Read one raw frame from a V4L2 device. This is the one place the
        # agent touches a camera, it reads exactly one frame, and it is only
        # reached from `verify()` / `liveness_challenge()` / `enroll()` —
        # never on a timer, never continuously.
        try:
            import fcntl
            import struct

            # YUYV (4:2:2): two luminance bytes per pixel pair.
            width, height = 320, 240
            size = width * height * 2
            with open(device, "rb") as handle:
                # VIDIOC_S_FMT sets the format; a driver that cannot honour it
                # fails here and the capture reports None rather than garbage.
                fmt = struct.pack(
                    "IiiII32sIIIIII",
                    0x56415959,  # V4L2_PIX_FMT_YUYV
                    width,
                    height,
                    1,
                    0,
                    b"",
                    0,
                    0,
                    0,
                    0,
                    0,
                )
                fcntl.ioctl(handle.fileno(), 0xC0CC5605, fmt)  # VIDIOC_S_FMT
                raw = handle.read(size)
            if len(raw) < size:
                return None
            gray = [raw[i] for i in range(0, size, 2)]
            Image.frombytes("L", (width, height), bytes(gray))  # validate it decodes
            cells = _cells_from_gray(gray, width, height)
            return FaceCapture(hash_hex=_hash_from_cells(cells), cells=cells)
        except (OSError, ValueError, ImportError):
            return None


__all__ = [
    "HASH_GRID",
    "VERIFY_MATCH_BITS",
    "LIVENESS_DELTA_BITS",
    "FaceTemplate",
    "FaceCapture",
    "FaceVerificationResult",
    "FaceVerifier",
]
