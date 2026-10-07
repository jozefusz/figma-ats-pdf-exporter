"""Thin wrapper around the Figma REST API for pulling node trees."""

from __future__ import annotations

import requests

API_BASE = "https://api.figma.com/v1"


class FigmaAPIError(RuntimeError):
    pass


def get_node(file_key: str, node_id: str, token: str) -> dict:
    """Fetch a single node (and its full subtree) from a Figma file.

    The REST API's /nodes endpoint returns geometry/style resolved in the
    same units the plugin API uses (px, top-left origin), which is what the
    PDF builder needs for absolute positioning.
    """
    url = f"{API_BASE}/files/{file_key}/nodes"
    resp = requests.get(
        url,
        # geometry=paths pulls fillGeometry/strokeGeometry SVG path data for
        # vector nodes (icons) -- omitted by default to keep payloads small.
        params={"ids": node_id, "geometry": "paths"},
        headers={"X-Figma-Token": token},
        timeout=120,
    )
    if resp.status_code != 200:
        raise FigmaAPIError(
            f"Figma API request failed ({resp.status_code}): {resp.text[:500]}"
        )
    data = resp.json()
    nodes = data.get("nodes", {})
    entry = nodes.get(node_id)
    if entry is None:
        raise FigmaAPIError(
            f"Node {node_id} not found in response for file {file_key}. "
            f"Returned node ids: {list(nodes.keys())}"
        )
    if entry.get("document") is None:
        raise FigmaAPIError(f"Node {node_id} returned no document payload: {entry}")
    return entry["document"]
