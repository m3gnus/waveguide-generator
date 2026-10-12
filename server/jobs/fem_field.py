"""Subset the F8 lossless sidecar into the existing FEM display wire format."""
from __future__ import annotations

import base64
from pathlib import Path
import zipfile

import numpy as np


class FemFieldCorrupt(ValueError):
    """A retained sidecar does not satisfy the F8 storage contract."""


class FemFieldInvalidSelection(ValueError):
    """A requested frequency or channel is not retained."""


def _encode(array: np.ndarray, dtype: str) -> str:
    converted = np.asarray(array, dtype=dtype)
    if converted.dtype.kind == "f" and not np.isfinite(converted).all():
        raise FemFieldCorrupt("FEM field cannot be represented as finite display values")
    return base64.b64encode(converted.tobytes(order="C")).decode("ascii")


def _pressure_slice(archive: zipfile.ZipFile, frequency: int, channel: int,
                    shape: tuple[int, int, int]) -> np.ndarray:
    # NPZ members are compressed NPY streams. Skipping discards preceding bytes in
    # bounded blocks; it never allocates the whole frequency/channel tensor.
    with archive.open("pressure.npy") as stream:
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            stored_shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
        elif version == (2, 0):
            stored_shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
        else:
            raise FemFieldCorrupt("Unsupported FEM pressure array version")
        if stored_shape != shape or dtype != np.dtype("complex128"):
            raise FemFieldCorrupt("FEM pressure must be a complex128 frequency/channel/node array")
        count = shape[2]
        if fortran:
            # Fortran NPY members interleave frequencies/channels per node.
            # Select the requested strided response in bounded read blocks.
            pressure = np.empty(count, dtype=dtype)
            stride = shape[0] * shape[1]
            selected = frequency + shape[0] * channel
            position = written = 0
            total = stride * count
            while position < total:
                entries = min(65536, total - position)
                raw = stream.read(entries * dtype.itemsize)
                if len(raw) != entries * dtype.itemsize:
                    raise FemFieldCorrupt("FEM pressure slice is truncated")
                block = np.frombuffer(raw, dtype=dtype)
                part = block[(selected - position) % stride::stride]
                pressure[written:written + len(part)] = part
                written += len(part)
                position += entries
            if not np.isfinite(pressure).all():
                raise FemFieldCorrupt("FEM pressure is not finite")
            return pressure
        offset = (frequency * shape[1] + channel) * count * dtype.itemsize
        remaining = offset
        while remaining:
            discarded = stream.read(min(remaining, 1024 * 1024))
            if not discarded:
                raise FemFieldCorrupt("FEM pressure slice is truncated")
            remaining -= len(discarded)
        raw = stream.read(count * dtype.itemsize)
        if len(raw) != count * dtype.itemsize:
            raise FemFieldCorrupt("FEM pressure slice is truncated")
        pressure = np.frombuffer(raw, dtype=dtype)
        if not np.isfinite(pressure).all():
            raise FemFieldCorrupt("FEM pressure is not finite")
        return pressure


def load_fem_field_payload(path: Path, frequency: float | None,
                           channel: str | None) -> dict:
    """Read topology and just one frequency/channel, with local indices rebased."""
    try:
        with np.load(path, allow_pickle=False) as arrays:
            schema = str(arrays["schema"].item())
            if schema not in {"wg-fem-field-store-v1", "wg-fem-field-store-v2"}:
                raise FemFieldCorrupt("Unsupported FEM sidecar schema")
            if str(arrays["phasor_convention"].item()) != "exp(-i omega t)":
                raise FemFieldCorrupt("Unsupported FEM field conventions")
            frequencies = np.asarray(arrays["frequencies_hz"])
            channels = arrays["channel_ids"].tolist()
            regions = arrays["region_ids"].tolist()
            if (frequencies.ndim != 1 or not frequencies.size
                    or not np.isfinite(frequencies).all() or (frequencies <= 0).any()
                    or not isinstance(channels, list) or not channels
                    or not isinstance(regions, list) or not regions
                    or any(not isinstance(value, str) or not value for value in channels + regions)
                    or len(set(channels)) != len(channels) or len(set(regions)) != len(regions)):
                raise FemFieldCorrupt("Invalid FEM field axes")
            if frequency is None:
                frequency_index = 0
            else:
                matches = np.flatnonzero(np.isclose(frequencies, frequency, rtol=1e-9, atol=0))
                if not len(matches):
                    raise FemFieldInvalidSelection("Frequency is not retained in this FEM field")
                frequency_index = int(matches[0])
            selected_channel = channels[0] if channel is None else channel
            if selected_channel not in channels:
                raise FemFieldInvalidSelection("Channel is not retained in this FEM field")
            channel_index = channels.index(selected_channel)
            reference_voltage = None
            if schema == "wg-fem-field-store-v1":
                normalization = str(arrays["drive_normalization"].item())
                if normalization != "unit_acceleration_m_per_s2":
                    raise FemFieldCorrupt("Unsupported FEM field normalization")
            else:
                normalizations = arrays["drive_normalization"].tolist()
                if (not isinstance(normalizations, list) or len(normalizations) != len(channels)
                        or any(n not in {"unit_acceleration", "voltage_rms"} for n in normalizations)):
                    raise FemFieldCorrupt("Invalid per-channel FEM field normalization")
                normalization = normalizations[channel_index]
                if "voltage_rms" in normalizations:
                    reference_voltage = float(arrays["transducer_reference_voltage_v"].item())
                    if not np.isfinite(reference_voltage) or reference_voltage <= 0:
                        raise FemFieldCorrupt("Invalid FEM reference voltage")
            points, tetrahedra, surfaces = [], [], []
            offset = 0
            for region_index, _region in enumerate(regions):
                xyz = np.asarray(arrays[f"points_mm_{region_index}"])
                tets = np.asarray(arrays[f"tetrahedra_{region_index}"])
                if (xyz.ndim != 2 or xyz.shape[1] != 3 or not len(xyz)
                        or not np.isfinite(xyz).all()):
                    raise FemFieldCorrupt("Invalid FEM coordinates")
                def indices(value, width):
                    if (value.ndim != 2 or value.shape[1] != width
                            or value.dtype.kind not in "iu"
                            or (value < 0).any() or (value >= len(xyz)).any()
                            or offset + len(xyz) > np.iinfo(np.uint32).max):
                        raise FemFieldCorrupt("Invalid FEM topology")
                    return value.astype(np.uint64) + offset
                tetrahedra.append(indices(tets, 4))
                for surface_index, name in enumerate(arrays[f"surface_names_{region_index}"].tolist()):
                    triangles = arrays[f"surface_triangles_{region_index}_{surface_index}"]
                    surfaces.append({"name": str(name), "triangles": _encode(indices(triangles, 3), "<u4")})
                points.append(xyz)
                offset += len(xyz)
            pressure = _pressure_slice(arrays.zip, frequency_index, channel_index,
                                       (len(frequencies), len(channels), offset))
            return {
                "schema": "wg-fem-field-v1",
                "region_id": regions[0] if len(regions) == 1 else "all",
                "frequency_hz": float(frequencies[frequency_index]),
                "points_mm": _encode(np.concatenate(points), "<f4"),
                "tetrahedra": _encode(np.concatenate(tetrahedra), "<u4"),
                "surfaces": surfaces,
                "pressure_real": _encode(pressure.real, "<f4"),
                "pressure_imag": _encode(pressure.imag, "<f4"),
                "frequencies_hz": frequencies.tolist(),
                "channel_ids": channels,
                "channel_id": selected_channel,
                "phasor_convention": "exp(-i omega t)",
                "drive_normalization": normalization,
                **({"transducer_reference_voltage_v": reference_voltage}
                   if normalization == "voltage_rms" else {}),
            }
    except (FemFieldCorrupt, FemFieldInvalidSelection):
        raise
    except (KeyError, IndexError, TypeError, ValueError, zipfile.BadZipFile, EOFError) as exc:
        raise FemFieldCorrupt("FEM field sidecar is corrupt") from exc
