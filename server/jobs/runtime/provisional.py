"""Accumulation and copying of provisional solve result deltas."""

from __future__ import annotations

import copy
from typing import Any, Mapping


def _sort_provisional_frequencies(result: dict[str, Any]) -> None:
    """Keep accumulated chart rows ascending while deltas arrive out of order."""

    frequencies = result.get("frequencies")
    if isinstance(frequencies, list) and len(frequencies) > 1:
        order = sorted(range(len(frequencies)), key=frequencies.__getitem__)
        if order != list(range(len(frequencies))):
            count = len(frequencies)
            result["frequencies"] = [frequencies[index] for index in order]
            if isinstance(result.get("frequency_status"), list):
                result["frequency_status"] = [result["frequency_status"][index] for index in order]
            for block_name in ("directivity", "directivity_phase"):
                block = result.get(block_name)
                if not isinstance(block, dict):
                    continue
                for plane, rows in block.items():
                    if isinstance(rows, list) and len(rows) == count:
                        block[plane] = [rows[index] for index in order]
            for block_name in ("spl_on_axis", "impedance", "di"):
                block = result.get(block_name)
                if not isinstance(block, dict):
                    continue
                for key, values in block.items():
                    if isinstance(values, list) and len(values) == count:
                        block[key] = [values[index] for index in order]
    channels = result.get("channels")
    if isinstance(channels, dict):
        for channel in channels.values():
            if isinstance(channel, dict):
                _sort_provisional_frequencies(channel)


def _extend_provisional_results(
    current: dict[str, Any], delta: Mapping[str, Any]
) -> dict[str, Any]:
    """Extend the runtime-owned accumulator without recopying prior rows."""

    def append_list(container: dict[str, Any], source: Mapping[str, Any], key: str) -> None:
        incoming = source.get(key)
        if not isinstance(incoming, list):
            return
        existing = container.get(key)
        if isinstance(existing, list):
            existing.extend(copy.deepcopy(incoming))
        else:
            container[key] = copy.deepcopy(incoming)

    if isinstance(delta.get("frequency_status"), list):
        for key in ("frequencies", "frequency_status", "directivity", "directivity_phase",
                    "spl_on_axis", "impedance", "di", "balloon", "beam_shape"):
            current.pop(key, None)
        for key in ("balloon", "beam_shape"):
            if key in delta:
                current[key] = copy.deepcopy(delta[key])
    append_list(current, delta, "frequencies")
    append_list(current, delta, "frequency_status")
    for block_name in ("directivity", "directivity_phase"):
        incoming_block = delta.get(block_name)
        if not isinstance(incoming_block, Mapping):
            continue
        block = current.get(block_name)
        block = block if isinstance(block, dict) else {}
        for plane, rows in incoming_block.items():
            if not isinstance(rows, list):
                continue
            existing = block.get(str(plane))
            if isinstance(existing, list):
                existing.extend(copy.deepcopy(rows))
            else:
                block[str(plane)] = copy.deepcopy(rows)
        current[block_name] = block

    for block_name in ("spl_on_axis", "impedance", "di"):
        incoming_block = delta.get(block_name)
        if not isinstance(incoming_block, Mapping):
            continue
        block = current.get(block_name)
        block = block if isinstance(block, dict) else {}
        for key in incoming_block:
            append_list(block, incoming_block, str(key))
        current[block_name] = block

    incoming_channels = delta.get("channels")
    if isinstance(incoming_channels, Mapping):
        channels = current.get("channels")
        channels = channels if isinstance(channels, dict) else {}
        for channel_id, channel_delta in incoming_channels.items():
            if not isinstance(channel_delta, Mapping):
                continue
            existing = channels.get(str(channel_id))
            channel = existing if isinstance(existing, dict) else {}
            channels[str(channel_id)] = _extend_provisional_results(
                channel, channel_delta
            )
        current["channels"] = channels

    if isinstance(delta.get("channel_order"), list):
        current["channel_order"] = copy.deepcopy(delta["channel_order"])
    if isinstance(delta.get("metadata"), Mapping):
        prior_metadata = current.get("metadata")
        current["metadata"] = {
            **(
                dict(prior_metadata)
                if isinstance(prior_metadata, Mapping)
                else {}
            ),
            **copy.deepcopy(dict(delta["metadata"])),
        }
    _sort_provisional_frequencies(current)
    return current


def _unshare_provisional_rows(result: dict[str, Any]) -> None:
    """Give every list the accumulator appends to an object of its own."""

    if isinstance(result.get("frequency_status"), list):
        result["frequency_status"] = list(result["frequency_status"])
    if isinstance(result.get("frequencies"), list):
        result["frequencies"] = list(result["frequencies"])
    for block_name in ("directivity", "directivity_phase", "spl_on_axis", "impedance", "di"):
        block = result.get(block_name)
        if isinstance(block, dict):
            result[block_name] = {
                key: list(values) if isinstance(values, list) else values
                for key, values in block.items()
            }
    channels = result.get("channels")
    if isinstance(channels, dict):
        unshared: dict[Any, Any] = {}
        for channel_id, channel in channels.items():
            if isinstance(channel, dict):
                channel = dict(channel)
                _unshare_provisional_rows(channel)
            unshared[channel_id] = channel
        result["channels"] = unshared


def _copy_provisional_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy a result into an accumulator whose appended lists share nothing.

    ``build_solver_response`` hands one ``frequencies`` list to the top level
    and to ``spl_on_axis``, ``impedance`` and ``di``. ``copy.deepcopy`` keeps
    that sharing, so every later frequency was appended once per reference: a
    streamed multi-channel Metal channel read seven frequencies against three
    directivity rows after three frames.
    """

    snapshot = copy.deepcopy(dict(result))
    _unshare_provisional_rows(snapshot)
    return snapshot


def merge_provisional_results(
    current: Mapping[str, Any] | None, delta: Mapping[str, Any]
) -> dict[str, Any]:
    """Append one frequency-shaped result delta without mutating either input."""

    if current is None:
        return _copy_provisional_result(delta)
    return _extend_provisional_results(_copy_provisional_result(current), delta)
