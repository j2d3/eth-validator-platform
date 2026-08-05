#!/usr/bin/env python3
"""Curated, read-only Prometheus adapter for the operator portal."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


LOG = logging.getLogger("portal-status-api")
SCHEMA_VERSION = 1
PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://prometheus-operated.observability.svc.cluster.local:9090",
).rstrip("/")
GRAFANA_BASE_URL = os.environ.get("GRAFANA_BASE_URL", "").rstrip("/")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://g.j2d3.com")
CLUSTER_NAME = os.environ.get("CLUSTER_NAME", "eth-validator-platform-dev")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "dev")
LISTEN_ADDRESS = os.environ.get("LISTEN_ADDRESS", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
QUERY_TIMEOUT_SECONDS = float(os.environ.get("QUERY_TIMEOUT_SECONDS", "3"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "15"))
STALE_TTL_SECONDS = int(os.environ.get("STALE_TTL_SECONDS", "60"))


SCALAR_QUERIES = {
    "sourceReady": (
        'min(up{namespace="observability",'
        'service="kube-prometheus-stack-kube-state-metrics"})'
    ),
    "nodesReady": 'sum(kube_node_status_condition{condition="Ready",status="true"})',
    "clusterCpuAllocatableCores": (
        'sum(kube_node_status_allocatable{resource="cpu",unit="core"})'
    ),
    "clusterMemoryAllocatableBytes": (
        'sum(kube_node_status_allocatable{resource="memory",unit="byte"})'
    ),
    "clusterCpuUsageCores": (
        'sum(rate(container_cpu_usage_seconds_total{container!="",image!=""}[5m]))'
    ),
    "clusterMemoryUsageBytes": (
        'sum(container_memory_working_set_bytes{container!="",image!=""})'
    ),
    "clusterPods": "sum(kube_pod_info)",
    "clusterPodsRunning": 'sum(kube_pod_status_phase{phase="Running"})',
    "clusterPodsPending": 'sum(kube_pod_status_phase{phase="Pending"})',
    "clusterContainerRestarts": "sum(kube_pod_container_status_restarts_total)",
    "nodeGroupLabels": (
        "count(kube_node_labels{"
        'label_eks_amazonaws_com_nodegroup!=""})'
    ),
    "systemNodesReady": (
        'sum(kube_node_status_condition{condition="Ready",status="true"} '
        "* on(node) group_left(label_eks_amazonaws_com_nodegroup) "
        "kube_node_labels{"
        'label_eks_amazonaws_com_nodegroup=~"system-.*"})'
    ),
    "ethereumNodesReady": (
        'sum(kube_node_status_condition{condition="Ready",status="true"} '
        "* on(node) group_left(label_eks_amazonaws_com_nodegroup) "
        "kube_node_labels{"
        'label_eks_amazonaws_com_nodegroup=~"ethereum-.*"}) or vector(0)'
    ),
    "ethereumPods": 'sum(kube_pod_info{namespace="ethereum"}) or vector(0)',
    "ethereumPodsRunning": (
        'sum(kube_pod_status_phase{namespace="ethereum",phase="Running"}) '
        "or vector(0)"
    ),
    "ethereumCpuCores": (
        "sum(rate(container_cpu_usage_seconds_total{namespace=\"ethereum\","
        'container!="",image!=""}[5m])) or vector(0)'
    ),
    "ethereumMemoryBytes": (
        "sum(container_memory_working_set_bytes{namespace=\"ethereum\","
        'container!="",image!=""}) or vector(0)'
    ),
    "ethereumRestarts": (
        'sum(kube_pod_container_status_restarts_total{namespace="ethereum"}) '
        "or vector(0)"
    ),
    "ethereumVolumeUsedBytes": (
        'sum(kubelet_volume_stats_used_bytes{namespace="ethereum"}) or vector(0)'
    ),
    "ethereumVolumeCapacityBytes": (
        'sum(kubelet_volume_stats_capacity_bytes{namespace="ethereum"}) '
        "or vector(0)"
    ),
    "signerUp": "max(validator_platform_signer_up)",
    "signerKeysLoaded": "max(validator_platform_signer_keys_loaded)",
    "signingValidatorsEnabled": (
        "sum(validator_platform_validator_enabled) or vector(0)"
    ),
    "signingPermittedTotal": (
        "max(validator_platform_signer_slashing_permitted_total)"
    ),
    "signingPreventedTotal": (
        "max(validator_platform_signer_slashing_prevented_total)"
    ),
    "signingMissingIdentifierTotal": (
        "max(validator_platform_signer_missing_identifier_total)"
    ),
    "firingAlertsTotal": (
        'sum(ALERTS{alertstate="firing",alertname!="Watchdog"}) or vector(0)'
    ),
    "firingAlertsCritical": (
        'sum(ALERTS{alertstate="firing",alertname!="Watchdog",severity="critical"}) '
        "or vector(0)"
    ),
    "firingAlertsWarning": (
        'sum(ALERTS{alertstate="firing",alertname!="Watchdog",severity="warning"}) '
        "or vector(0)"
    ),
}

PAIR_QUERIES = {
    "targetUp": "validator_platform_pair_target_up",
    "validatorEnabled": "validator_platform_validator_enabled",
    "executionPeers": "validator_platform_execution_peers",
    "consensusPeers": "validator_platform_consensus_peers",
    "executionHeadBlock": "validator_platform_execution_head_block",
    "executionHeadHeader": "validator_platform_execution_head_header",
    "executionSyncDistance": "validator_platform_execution_internal_sync_distance",
    "executionHeadChanges15m": "validator_platform_execution_head_changes_15m",
    "consensusHeadChanges15m": "validator_platform_consensus_head_changes_15m",
    "consensusSlotLag": "validator_platform_consensus_slot_lag",
    "consensusFinalityLagEpochs": (
        "validator_platform_consensus_finality_lag_epochs"
    ),
    "containerCpuCores": "validator_platform_container_cpu_cores",
    "containerMemoryWorkingSetBytes": (
        "validator_platform_container_memory_working_set_bytes"
    ),
}

SAFE_PAIR_LABELS = (
    "assignment_id",
    "cluster",
    "environment",
    "network",
    "network_profile",
    "network_generation",
    "execution_client",
    "consensus_client",
    "lifecycle_state",
    "component",
)


class TelemetryUnavailable(RuntimeError):
    """The allowlisted Prometheus read did not produce a valid response."""


def _validate_configuration() -> None:
    prometheus = urllib.parse.urlsplit(PROMETHEUS_URL)
    if prometheus.scheme not in {"http", "https"} or not prometheus.hostname:
        raise ValueError("PROMETHEUS_URL must be an HTTP(S) origin")

    for name, value, required_path in (("ALLOWED_ORIGIN", ALLOWED_ORIGIN, ""),):
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError(f"{name} must use HTTPS")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError(f"{name} must not contain credentials, query, or fragment")
        if required_path and parsed.path.rstrip("/") != required_path:
            raise ValueError(f"{name} must end at {required_path}")
        if not required_path and parsed.path not in {"", "/"}:
            raise ValueError(f"{name} must be a bare origin")

    if GRAFANA_BASE_URL:
        parsed = urllib.parse.urlsplit(GRAFANA_BASE_URL)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("GRAFANA_BASE_URL must use HTTPS")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError(
                "GRAFANA_BASE_URL must not contain credentials, query, or fragment"
            )
        if parsed.path.rstrip("/") != "/grafana":
            raise ValueError("GRAFANA_BASE_URL must end at /grafana")


def _number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 6)


class PrometheusClient:
    def __init__(self, base_url: str = PROMETHEUS_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def query(self, expression: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"query": expression})
        request = urllib.request.Request(
            f"{self.base_url}/api/v1/query?{query}",
            headers={"Accept": "application/json", "User-Agent": "portal-status-api/1"},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=QUERY_TIMEOUT_SECONDS
            ) as response:
                payload = json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            raise TelemetryUnavailable("Prometheus request failed") from error

        if payload.get("status") != "success":
            raise TelemetryUnavailable("Prometheus returned a non-success response")
        result = payload.get("data", {}).get("result")
        if not isinstance(result, list):
            raise TelemetryUnavailable("Prometheus result is not a vector")
        return result


def _scalar(result: list[dict[str, Any]]) -> int | float | None:
    if not result:
        return None
    if len(result) != 1:
        raise TelemetryUnavailable("Scalar query returned more than one series")
    value = result[0].get("value")
    if not isinstance(value, list) or len(value) != 2:
        raise TelemetryUnavailable("Scalar query returned an invalid sample")
    return _number(value[1])


def _safe_labels(metric: Any) -> dict[str, str]:
    if not isinstance(metric, dict):
        return {}
    return {
        label: str(metric[label])
        for label in SAFE_PAIR_LABELS
        if label in metric and metric[label] != ""
    }


def _sample(record: dict[str, Any]) -> int | float | None:
    value = record.get("value")
    if not isinstance(value, list) or len(value) != 2:
        return None
    return _number(value[1])


def _grafana_url(labels: dict[str, str]) -> str | None:
    if not GRAFANA_BASE_URL:
        return None
    variables = {
        "orgId": "1",
        "var-cluster": labels.get("cluster", CLUSTER_NAME),
        "var-network_profile": labels.get("network_profile", ".*"),
        "var-network_generation": labels.get("network_generation", ".*"),
        "var-assignment_id": labels["assignment_id"],
        "var-lifecycle": labels.get("lifecycle_state", ".*"),
    }
    query = urllib.parse.urlencode(variables)
    return (
        f"{GRAFANA_BASE_URL}/d/eth-eks-ephemery-sync/"
        f"ethereum-platform-eks-ephemery-sync-evidence?{query}"
    )


def _pair_snapshot(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    pairs: dict[str, dict[str, Any]] = {}
    for record in results["targetUp"]:
        labels = _safe_labels(record.get("metric"))
        assignment_id = labels.get("assignment_id")
        component = labels.get("component")
        value = _sample(record)
        if not assignment_id or component not in {"execution", "consensus"}:
            continue
        pair = pairs.setdefault(
            assignment_id,
            {
                "assignmentId": assignment_id,
                "cluster": labels.get("cluster"),
                "environment": labels.get("environment"),
                "network": labels.get("network"),
                "networkProfile": labels.get("network_profile"),
                "networkGeneration": labels.get("network_generation"),
                "executionClient": labels.get("execution_client"),
                "consensusClient": labels.get("consensus_client"),
                "lifecycleState": labels.get("lifecycle_state"),
                "targets": {},
                "signing": {"validatorsEnabled": None},
                "sync": {},
                "resources": {"cpuCores": {}, "memoryBytes": {}},
                "grafanaUrl": _grafana_url(labels),
            },
        )
        pair["targets"][component] = value

    scalar_pair_fields = {
        "validatorEnabled": ("signing", "validatorsEnabled"),
        "executionPeers": ("sync", "executionPeers"),
        "consensusPeers": ("sync", "consensusPeers"),
        "executionHeadBlock": ("sync", "executionHeadBlock"),
        "executionHeadHeader": ("sync", "executionHeadHeader"),
        "executionSyncDistance": ("sync", "executionSyncDistance"),
        "executionHeadChanges15m": ("sync", "executionHeadChanges15m"),
        "consensusHeadChanges15m": ("sync", "consensusHeadChanges15m"),
        "consensusSlotLag": ("sync", "consensusSlotLag"),
        "consensusFinalityLagEpochs": ("sync", "consensusFinalityLagEpochs"),
    }
    for metric_name, (section, field) in scalar_pair_fields.items():
        for record in results[metric_name]:
            assignment_id = _safe_labels(record.get("metric")).get("assignment_id")
            if assignment_id in pairs:
                pairs[assignment_id][section][field] = _sample(record)

    for metric_name, resource_name in (
        ("containerCpuCores", "cpuCores"),
        ("containerMemoryWorkingSetBytes", "memoryBytes"),
    ):
        for record in results[metric_name]:
            labels = _safe_labels(record.get("metric"))
            assignment_id = labels.get("assignment_id")
            component = labels.get("component")
            if assignment_id in pairs and component in {"execution", "consensus"}:
                pairs[assignment_id]["resources"][resource_name][component] = _sample(
                    record
                )

    return [pairs[key] for key in sorted(pairs)]


def build_snapshot(client: PrometheusClient | Any) -> dict[str, Any]:
    expressions = {**SCALAR_QUERIES, **PAIR_QUERIES}
    results: dict[str, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(client.query, expression): name
            for name, expression in expressions.items()
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    scalars = {name: _scalar(results[name]) for name in SCALAR_QUERIES}
    source_ready = scalars["sourceReady"] == 1
    node_group_labels_ready = (scalars["nodeGroupLabels"] or 0) > 0
    pairs = _pair_snapshot(results)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": {
            "prometheusReady": source_ready,
            "stale": False,
            "cacheAgeSeconds": 0,
        },
        "cluster": {
            "name": CLUSTER_NAME,
            "environment": ENVIRONMENT,
            "nodes": {
                "ready": scalars["nodesReady"],
                "systemReady": (
                    scalars["systemNodesReady"] if node_group_labels_ready else None
                ),
                "ethereumReady": (
                    scalars["ethereumNodesReady"] if node_group_labels_ready else None
                ),
            },
            "capacity": {
                "cpuCores": scalars["clusterCpuAllocatableCores"],
                "memoryBytes": scalars["clusterMemoryAllocatableBytes"],
            },
            "usage": {
                "cpuCores": scalars["clusterCpuUsageCores"],
                "memoryBytes": scalars["clusterMemoryUsageBytes"],
            },
            "pods": {
                "total": scalars["clusterPods"],
                "running": scalars["clusterPodsRunning"],
                "pending": scalars["clusterPodsPending"],
            },
            "containerRestarts": scalars["clusterContainerRestarts"],
            "ethereumWorkloads": {
                "pods": scalars["ethereumPods"],
                "podsRunning": scalars["ethereumPodsRunning"],
                "cpuCores": scalars["ethereumCpuCores"],
                "memoryBytes": scalars["ethereumMemoryBytes"],
                "containerRestarts": scalars["ethereumRestarts"],
                "persistentVolumeBytes": {
                    "used": scalars["ethereumVolumeUsedBytes"],
                    "capacity": scalars["ethereumVolumeCapacityBytes"],
                },
            },
        },
        "signing": {
            "validatorsEnabled": scalars["signingValidatorsEnabled"],
            "signerUp": scalars["signerUp"],
            "keysLoaded": scalars["signerKeysLoaded"],
            "slashingPermittedTotal": scalars["signingPermittedTotal"],
            "slashingPreventedTotal": scalars["signingPreventedTotal"],
            "missingIdentifierTotal": scalars["signingMissingIdentifierTotal"],
        },
        "alerts": {
            "firingTotal": scalars["firingAlertsTotal"],
            "critical": scalars["firingAlertsCritical"],
            "warning": scalars["firingAlertsWarning"],
        },
        "pairs": pairs,
    }


_cache_lock = threading.Lock()
_cached_snapshot: dict[str, Any] | None = None
_cached_at = 0.0


def get_snapshot(client: PrometheusClient | None = None) -> dict[str, Any]:
    global _cached_at, _cached_snapshot
    now = time.monotonic()
    with _cache_lock:
        if _cached_snapshot is not None and now - _cached_at < CACHE_TTL_SECONDS:
            snapshot = copy.deepcopy(_cached_snapshot)
            snapshot["source"]["cacheAgeSeconds"] = round(now - _cached_at, 3)
            return snapshot

        try:
            snapshot = build_snapshot(client or PrometheusClient())
        except (TelemetryUnavailable, TimeoutError, OSError):
            if _cached_snapshot is None or now - _cached_at >= STALE_TTL_SECONDS:
                raise
            snapshot = copy.deepcopy(_cached_snapshot)
            snapshot["source"]["stale"] = True
            snapshot["source"]["cacheAgeSeconds"] = round(now - _cached_at, 3)
            return snapshot

        _cached_snapshot = copy.deepcopy(snapshot)
        _cached_at = now
        return snapshot


class StatusHandler(BaseHTTPRequestHandler):
    server_version = "portal-status-api/1"
    sys_version = ""

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        origin = self.headers.get("Origin")
        if origin == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if self.headers.get("Origin") != ALLOWED_ORIGIN:
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        if path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if path not in {"/readyz", "/v1/status"}:
            self._send_json(404, {"error": "not_found"})
            return
        try:
            snapshot = get_snapshot()
        except (TelemetryUnavailable, TimeoutError, OSError):
            self._send_json(503, {"error": "telemetry_unavailable"})
            return
        if path == "/readyz":
            status = 200 if snapshot["source"]["prometheusReady"] else 503
            self._send_json(status, {"status": "ready" if status == 200 else "not_ready"})
            return
        self._send_json(200, snapshot)

    def log_message(self, message: str, *args: Any) -> None:
        LOG.info(message, *args)


def main() -> None:
    _validate_configuration()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((LISTEN_ADDRESS, LISTEN_PORT), StatusHandler)
    LOG.info("listening address=%s port=%s", LISTEN_ADDRESS, LISTEN_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
