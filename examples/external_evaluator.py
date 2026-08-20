#!/usr/bin/env python3
"""Evaluate one canonical WG SolveRequest through the public HTTP contract."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SUPPORTED_RESULT_CONTRACTS = {
    ("parametric", 1),
    ("multi_channel", 2),
}


class WgApiError(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        self.code = str(error.get("code") or f"http_{status}")
        self.stage = str(error.get("stage") or "http")
        self.retryable = bool(error.get("retryable"))
        message = str(error.get("message") or body.get("detail") or self.code)
        super().__init__(f"{self.code} at {self.stage}: {message}")


@dataclass(frozen=True)
class Evaluation:
    job_id: str
    status: dict[str, Any]
    results: dict[str, Any]
    result_sha256: str


class WaveguideGeneratorClient:
    def __init__(self, base_url: str = "http://127.0.0.1:3100") -> None:
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=60) as response:
                return response.read(), {key.lower(): value for key, value in response.headers.items()}
        except HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                body = {"detail": raw.decode("utf-8", errors="replace")}
            raise WgApiError(exc.code, body) from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach Waveguide Generator at {self.base_url}: {exc}") from exc

    def get_json(self, path: str) -> dict[str, Any]:
        raw, _headers = self._request("GET", path)
        return json.loads(raw)

    def capabilities(self) -> dict[str, Any]:
        return self.get_json("/api/capabilities")

    def parameter_catalog(self) -> dict[str, Any]:
        return self.get_json("/api/integration/v1/parameters")

    def submit(self, solve_request: dict[str, Any]) -> str:
        raw, _headers = self._request("POST", "/api/solve", solve_request)
        return str(json.loads(raw)["job_id"])

    def status(self, job_id: str) -> dict[str, Any]:
        return self.get_json(f"/api/status/{job_id}")

    def results(self, job_id: str) -> tuple[dict[str, Any], str]:
        raw, headers = self._request("GET", f"/api/results/{job_id}")
        digest = hashlib.sha256(raw).hexdigest()
        declared = headers.get("x-wg-results-sha256")
        if declared is None:
            raise RuntimeError("WG result response omitted X-WG-Results-SHA256")
        if declared != digest:
            raise RuntimeError(
                f"WG result digest mismatch: declared {declared}, received {digest}"
            )
        results = json.loads(raw)
        if not isinstance(results, dict):
            raise RuntimeError("WG result response must be a JSON object")
        contract = (
            results.get("result_kind"),
            results.get("result_contract_version"),
        )
        if contract not in SUPPORTED_RESULT_CONTRACTS:
            raise RuntimeError(
                "Unsupported WG result contract: "
                f"kind={contract[0]!r}, version={contract[1]!r}"
            )
        return results, digest

    def evaluate(
        self,
        solve_request: dict[str, Any],
        *,
        poll_seconds: float = 0.25,
        timeout_seconds: float = 3600.0,
    ) -> Evaluation:
        job_id = self.submit(solve_request)
        deadline = time.monotonic() + timeout_seconds
        while True:
            status = self.status(job_id)
            if status["status"] == "complete":
                results, digest = self.results(job_id)
                return Evaluation(job_id, status, results, digest)
            if status["status"] in {"error", "cancelled"}:
                raise RuntimeError(
                    f"WG job {job_id} ended as {status['status']}: "
                    f"{status.get('error_message') or status.get('message') or 'no detail'}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"WG job {job_id} did not finish within {timeout_seconds:g}s")
            time.sleep(max(0.01, poll_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="canonical SolveRequest JSON")
    parser.add_argument("--base-url", default="http://127.0.0.1:3100")
    parser.add_argument("--output", type=Path, help="write the returned result JSON")
    args = parser.parse_args(argv)

    solve_request = json.loads(args.request.read_text(encoding="utf-8"))
    evaluation = WaveguideGeneratorClient(args.base_url).evaluate(solve_request)
    summary = {
        "job_id": evaluation.job_id,
        "result_kind": evaluation.results.get("result_kind"),
        "result_contract_version": evaluation.results.get(
            "result_contract_version"
        ),
        "result_sha256": evaluation.result_sha256,
    }
    if args.output is not None:
        output_bytes = (
            json.dumps(
                evaluation.results,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        args.output.write_bytes(output_bytes)
        summary["output_sha256"] = hashlib.sha256(output_bytes).hexdigest()
    print(
        json.dumps(
            summary,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
