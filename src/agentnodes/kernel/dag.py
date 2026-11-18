from __future__ import annotations

from collections import deque


def validate_dag(adj: dict[str, list[str]]) -> None:
    """Validate that ``adj`` is a valid acyclic DAG.

    Convention: ``{node: [upstream_deps]}``.  A node with an empty
    list has no dependencies (root).  A cycle in the dependency
    graph will raise ``ValueError``.
    """
    all_nodes: set[str] = set(adj)
    for deps in adj.values():
        all_nodes.update(deps)

    reverse: dict[str, list[str]] = {n: [] for n in all_nodes}
    for node, deps in adj.items():
        for dep in deps:
            reverse.setdefault(dep, []).append(node)

    in_degree: dict[str, int] = {n: len(adj.get(n, [])) for n in all_nodes}

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    visited = 0

    while queue:
        node = queue.popleft()
        visited += 1
        for dependent in reverse.get(node, []):
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if visited != len(all_nodes):
        raise ValueError("DAG contains a cycle")


def topological_layers(adj: dict[str, list[str]]) -> list[list[str]]:
    """Split a DAG into execution layers via topological sort.

    Convention: ``{node: [upstream_deps]}``.  Nodes with no deps
    (in_degree 0) execute first, then their dependents, and so on.

    Raises:
        ValueError: If the DAG contains a cycle (call ``validate_dag``
                    first for a clean error, or catch this).
    """
    if not adj:
        return []

    all_nodes: set[str] = set(adj)
    for deps in adj.values():
        all_nodes.update(deps)

    reverse: dict[str, list[str]] = {n: [] for n in all_nodes}
    for node, deps in adj.items():
        for dep in deps:
            reverse.setdefault(dep, []).append(node)

    in_degree: dict[str, int] = {n: len(adj.get(n, [])) for n in all_nodes}

    queue: deque[str] = deque(n for n, d in in_degree.items() if d == 0)
    layers: list[list[str]] = []

    while queue:
        layer = list(queue)
        layers.append(sorted(layer))
        queue.clear()
        for node in layer:
            for dependent in reverse.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

    if not layers:
        raise ValueError("DAG contains a cycle (no nodes with in_degree=0)")

    return layers


def find_downstream(adj: dict[str, list[str]], node: str) -> list[str]:
    """Find all downstream nodes that depend on ``node`` (BFS).

    TODO(architecture): currently no production callers — utilities for
    the planned DAG analytics / targeted re-run (recompute only the
    downstream of a changed node). Keep for that feature.

    Convention: ``{node: [upstream_deps]}``.

    Returns a sorted list of all nodes reachable by following
    dependency edges *forward* from ``node`` — i.e., every node
    that directly or indirectly depends on ``node``.

    Args:
        adj: DAG in deps convention ``{node: [upstream_deps]}``.
        node: Starting node.

    Returns:
        Sorted list of downstream node names (excludes ``node`` itself).
    """
    # Build reverse adjacency: dep -> [nodes that depend on it]
    reverse: dict[str, list[str]] = {}
    for n, deps in adj.items():
        for dep in deps:
            reverse.setdefault(dep, []).append(n)

    visited: set[str] = set()
    queue: deque[str] = deque()
    for child in reverse.get(node, []):
        if child not in visited:
            visited.add(child)
            queue.append(child)

    while queue:
        current = queue.popleft()
        for child in reverse.get(current, []):
            if child not in visited:
                visited.add(child)
                queue.append(child)

    return sorted(visited)


def find_upstream(adj: dict[str, list[str]], node: str) -> list[str]:
    """Find all upstream nodes that ``node`` depends on (BFS).

    Convention: ``{node: [upstream_deps]}``.

    Returns a sorted list of all nodes reachable by following
    dependency edges *backward* from ``node`` — i.e., every node
    that ``node`` directly or indirectly depends on.

    Args:
        adj: DAG in deps convention ``{node: [upstream_deps]}``.
        node: Starting node.

    Returns:
        Sorted list of upstream node names (excludes ``node`` itself).
    """
    visited: set[str] = set()
    queue: deque[str] = deque()
    for dep in adj.get(node, []):
        if dep not in visited:
            visited.add(dep)
            queue.append(dep)

    while queue:
        current = queue.popleft()
        for dep in adj.get(current, []):
            if dep not in visited:
                visited.add(dep)
                queue.append(dep)

    return sorted(visited)
