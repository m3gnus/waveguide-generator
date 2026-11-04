"""Passive wiring over coupled BEAT voltage bases, without another BEM solve.

Circuit equations use engineering exp(+jwt), RMS volts/amps and SI parts.
Retained acoustic/motion/current bases use the solver convention; conjugation
occurs only at that boundary. Driver ports form a dense admittance multiport,
so off-diagonal acoustic loading and prescribed-source induced currents survive.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from server.contracts.conventions import ENGINEERING_PHASE_CONVENTION, engineering_to_solver, solver_to_engineering

from .errors import RecombineError


@dataclass
class NetworkSolution:
    voltage: np.ndarray
    current: np.ndarray
    input_current: dict[str, complex]


def solve_networks(
    frequency_hz: float, admittance: np.ndarray, driver_ids: list[str], wiring: list[Any],
    *, source_voltage: Mapping[str, complex], fixed_voltage: Mapping[str, complex] | None = None,
    induced_current: np.ndarray | None = None,
) -> NetworkSolution:
    """Nodal elimination of ideal source nodes (the reduced MNA system).

    ``I = Y V + induced_current`` defines driver currents entering their positive
    terminals. An inverted leaf reverses both port voltage and circuit current.
    Unwired drivers have ideal voltage sources, zero unless explicitly driven.
    """
    if not np.isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError("Wiring needs positive finite frequencies.")
    n = len(driver_ids)
    y = np.asarray(admittance, dtype=complex)
    offset = np.zeros(n, complex) if induced_current is None else np.asarray(induced_current, complex)
    if y.shape != (n, n) or offset.shape != (n,) or not np.isfinite(y).all() or not np.isfinite(offset).all():
        raise ValueError("Invalid coupled driver admittance or induced-current tensor")
    known = {0: 0j}
    ports: dict[str, tuple[int, int]] = {}
    parts: list[tuple[int, int, complex]] = []
    source_nodes: dict[str, int] = {}
    count = 1

    def node():
        nonlocal count
        result = count
        count += 1
        return result

    def connect(tree, positive, negative):
        kind = tree.kind
        if kind == "driver":
            if tree.radiator_id in ports or tree.radiator_id not in driver_ids:
                raise ValueError("Wiring names an unknown or duplicate driver")
            ports[tree.radiator_id] = (negative, positive) if tree.invert else (positive, negative)
        elif kind in {"R", "L", "C"}:
            omega = 2 * np.pi * frequency_hz
            try:
                impedance = tree.value if kind == "R" else 1j * omega * tree.value if kind == "L" else 1 / (1j * omega * tree.value)
                value = 1 / impedance
            except (ZeroDivisionError, OverflowError) as exc:
                raise RecombineError(f"A wiring part is outside numerical range at {frequency_hz:g} Hz; use less extreme component values.") from exc
            if not np.isfinite(value):
                raise RecombineError(f"A wiring part is outside numerical range at {frequency_hz:g} Hz; use less extreme component values.")
            parts.append((positive, negative, value))
        else:
            start = positive
            for index, child in enumerate(tree.children):
                end = negative if kind == "parallel" or index == len(tree.children) - 1 else node()
                connect(child, start, end)
                if kind == "series":
                    start = end

    for group in wiring:
        source_nodes[group.id] = root = node()
        known[root] = complex(source_voltage.get(group.id, 0))
        connect(group.network, root, 0)
    for name in driver_ids:
        if name not in ports:
            positive = node()
            known[positive] = complex((fixed_voltage or {}).get(name, 0))
            ports[name] = (positive, 0)
    incidence = np.zeros((n, count))
    for index, name in enumerate(driver_ids):
        positive, negative = ports[name]
        incidence[index, positive] = 1
        incidence[index, negative] = -1
    matrix = incidence.T @ y @ incidence
    for positive, negative, value in parts:
        edge = np.zeros(count)
        edge[positive], edge[negative] = 1, -1
        matrix += value * np.outer(edge, edge)
    injection = incidence.T @ offset
    potential = np.zeros(count, complex)
    fixed = list(known)
    potential[fixed] = list(known.values())
    unknown = [index for index in range(count) if index not in known]
    if unknown:
        rhs = -injection[unknown] - matrix[np.ix_(unknown, fixed)] @ potential[fixed]
        try:
            potential[unknown] = np.linalg.solve(matrix[np.ix_(unknown, unknown)], rhs)
        except np.linalg.LinAlgError as exc:
            raise RecombineError(f"Wiring has no finite solution at {frequency_hz:g} Hz; check the component values and connections.") from exc
    voltage = incidence @ potential
    current = y @ voltage + offset
    terminal = matrix @ potential + injection
    if not all(np.isfinite(values).all() for values in (voltage, current, terminal)):
        raise RecombineError(f"Wiring has no finite solution at {frequency_hz:g} Hz; check the component values and connections.")
    return NetworkSolution(voltage, current, {name: terminal[index] for name, index in source_nodes.items()})


def retain_native_tensors(results, sweep, indices, channels, reference_voltage):
    """Attach per-channel columns in identity order, including ideal-source currents."""
    from urllib.parse import quote

    driver_ids = [channel.id for channel in channels if channel.exterior_transducer is not None]
    native_ids = list(sweep.rows[0].transducer_ids)
    if any(any(count != 1 for count in row.radiation.metadata["physical_driver_orbit_counts"]) for row in sweep.rows):
        raise ValueError("Wiring needs independently driven physical woofers; use a full-domain assembly mesh.")
    order = [native_ids.index(f"transducer:{quote(name, safe='')}") for name in driver_ids]
    for name, result in results.items():
        result.native_driver_ids = driver_ids
        result.native_reference_voltage = float(reference_voltage)
        result.native_current = np.stack([row.current_rms_a[indices[name]].sum(axis=0)[order] for row in sweep.rows])
        result.native_velocity = np.stack([row.velocity_rms_m_per_s[indices[name]].sum(axis=0)[order] for row in sweep.rows])


@dataclass
class NetworkBases:
    results: dict[str, Any]
    channel_ids: list[str]
    driver_ids: list[str]
    voltage: np.ndarray  # F, input basis, driver
    current: np.ndarray
    velocity: np.ndarray
    input_current: np.ndarray  # F, input basis, wiring group
    representatives: dict[str, str]
    wiring: list[Any]
    drive_voltage: float

    def diagnostics(self, weights: Mapping[str, np.ndarray]) -> dict:
        factors = np.stack([weights.get(name, np.zeros(self.voltage.shape[0])) for name in self.channel_ids], axis=1)

        def combine(tensor):
            return solver_to_engineering(np.einsum("fb,fbn->fn", factors, tensor))

        def wire(array):
            return {"real": array.real.tolist(), "imaginary": array.imag.tolist()}

        voltage, current, velocity = (combine(tensor) for tensor in (self.voltage, self.current, self.velocity))
        inputs = combine(self.input_current)
        networks = {}
        for index, group in enumerate(self.wiring):
            representative = self.representatives[group.id]
            column = self.channel_ids.index(representative)
            own_current = solver_to_engineering(self.input_current[:, column, index])
            impedance = np.divide(self.drive_voltage, own_current, out=np.full(own_current.shape, np.nan + 0j), where=np.abs(own_current) > 0)
            networks[group.id] = {
                "label": group.label,
                "input_impedance_ohm": {key: [float(v) if np.isfinite(v) else None for v in values]
                                        for key, values in wire(impedance).items()},
                "voltage_rms_v": wire(solver_to_engineering(weights.get(representative, np.zeros(len(inputs)))) * self.drive_voltage),
                "current_rms_a": wire(inputs[:, index]),
                "impedance_termination": "other amplifier sources shorted; prescribed sources rigid",
            }
        return {
            "version": 1, "phase_convention": ENGINEERING_PHASE_CONVENTION, "amplitude_convention": "rms",
            "networks": networks,
            "drivers": {name: {"voltage_rms_v": wire(voltage[:, i]), "current_rms_a": wire(current[:, i]),
                                "velocity_rms_m_per_s": wire(velocity[:, i])} for i, name in enumerate(self.driver_ids)},
        }


def prepare_network_bases(results: Mapping[str, Any], geometry: Any) -> NetworkBases:
    """Transform independent input columns before the shared DSP/group combine.

    One existing channel carries each amplifier's response and its other wired
    channels carry zero. Their signal-group sum is therefore exactly one network
    response. Every other input column includes motion induced in the terminated
    passive networks, including a prescribed HF input.
    """
    names = list(results)
    base = results[names[0]]
    if not hasattr(base, "native_current"):
        raise RecombineError("This run did not retain coupled current bases; solve the wired assembly again.")
    drivers = list(base.native_driver_ids)
    ref = base.native_reference_voltage
    frequencies = np.asarray(base.frequencies_hz)
    count = len(frequencies)
    currents = np.stack([results[name].native_current for name in names], axis=1)
    velocities = np.stack([results[name].native_velocity for name in names], axis=1)
    y = solver_to_engineering(currents[:, [names.index(name) for name in drivers], :].transpose(0, 2, 1)) / ref
    representatives = {group.id: group.driver_ids()[0] for group in geometry.wiring_groups}
    wired = {name for group in geometry.wiring_groups for name in group.driver_ids()}
    coefficients = np.zeros((count, len(names), len(names)), complex)
    voltage = np.zeros((count, len(names), len(drivers)), complex)
    inputs = np.zeros((count, len(names), len(geometry.wiring_groups)), complex)
    for column, name in enumerate(names):
        if name in wired and name not in representatives.values():
            continue
        for f, frequency in enumerate(frequencies):
            solution = solve_networks(float(frequency), y[f], drivers, geometry.wiring_groups,
                source_voltage={key: geometry.drive_voltage_v if rep == name else 0 for key, rep in representatives.items()},
                fixed_voltage={name: ref} if name in drivers and name not in wired else {},
                induced_current=solver_to_engineering(currents[f, column]) if name not in drivers else None)
            voltage[f, column] = engineering_to_solver(solution.voltage)
            inputs[f, column] = engineering_to_solver(np.asarray(list(solution.input_current.values())))
            for index, driver in enumerate(drivers):
                coefficients[f, column, names.index(driver)] = voltage[f, column, index] / ref
            if name not in drivers:
                coefficients[f, column, column] = 1
    transformed = {}
    for column, name in enumerate(names):
        result = copy(results[name])
        for field in ("pressure_complex", "sphere_pressure_complex"):
            values = [getattr(results[key], field, None) for key in names]
            if all(value is not None for value in values):
                result_value = np.einsum("fb,fb...->f...", coefficients[:, column], np.stack(values, axis=1))
                setattr(result, field, result_value)
            elif field == "sphere_pressure_complex":
                setattr(result, field, None)
        transformed[name] = result
    return NetworkBases(transformed, names, drivers, voltage,
        np.einsum("fbc,fcn->fbn", coefficients, currents),
        np.einsum("fbc,fcn->fbn", coefficients, velocities), inputs, representatives,
        geometry.wiring_groups, geometry.drive_voltage_v)
