"""Find the callers affected by the deprecated call sites (sub-component 2.6).

Owner: Verril (Module 2). Design: Module 2 detailed design, figure 4: a
breadth-first walk up the call graph from every function that contains a
call site, bounded by a depth limit. The visited set makes the walk visit
each function once, which also ends it on recursive call cycles.
"""

from __future__ import annotations

from collections import defaultdict, deque

from migration_agent.models import CallSite, ImpactSet


class ImpactAnalyzer:
    def __init__(self, depth_limit: int = 5) -> None:
        if depth_limit < 0:
            raise ValueError("depth_limit must be zero or more")
        self.depth_limit = depth_limit

    def compute(
        self,
        call_sites: list[CallSite],
        call_graph: dict[str, list[str]],
        function_files: dict[str, str],
    ) -> ImpactSet:
        callers: dict[str, set[str]] = defaultdict(set)
        for caller, callees in call_graph.items():
            for callee in callees:
                callers[callee].add(caller)

        depth: dict[str, int] = {}
        queue: deque[str] = deque()
        for site in call_sites:
            if site.enclosing_function not in depth:
                depth[site.enclosing_function] = 0
                queue.append(site.enclosing_function)
        while queue:
            function = queue.popleft()
            if depth[function] >= self.depth_limit:
                continue
            for caller in sorted(callers[function]):
                if caller not in depth:
                    depth[caller] = depth[function] + 1
                    queue.append(caller)

        files = {site.file for site in call_sites}
        files |= {function_files[f] for f in depth if f in function_files}
        return ImpactSet(
            functions=sorted(depth),
            files=sorted(files),
            depth=dict(sorted(depth.items())),
        )
