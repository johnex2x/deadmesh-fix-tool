from __future__ import annotations


def nothing_got_worse(
    baseline: dict,
    scan: dict,
    *,
    ignore: frozenset[str] = frozenset(),
) -> bool:
    baseline_verdict = baseline["verdict"].upper()
    verdict = scan["verdict"].upper()
    if scan["status"] == "BROKEN":
        return False
    for word in ("CRASH", "HANG"):
        if word in verdict:
            return False
    if "HEAVY" not in baseline_verdict and "HEAVY" in verdict:
        return False
    if scan["broken"]["refs"] != 0:
        return False
    if scan["freeze"]["cullVerdict"] > baseline["freeze"]["cullVerdict"]:
        return False
    if "orientation_inverted" not in ignore:
        if scan["orientation"]["inverted"] > baseline["orientation"]["inverted"]:
            return False
    if scan["orientation"]["mixed"] > baseline["orientation"]["mixed"]:
        return False
    if scan["orientation"]["worstTier"] > baseline["orientation"]["worstTier"]:
        return False
    if "winding_inverted" not in ignore:
        if scan["winding_cull"]["inverted"] > baseline["winding_cull"]["inverted"]:
            return False
    if scan["winding_cull"]["ambiguous"] > baseline["winding_cull"]["ambiguous"]:
        return False
    if scan["winding_cull"]["leak"] and not baseline["winding_cull"]["leak"]:
        return False
    if "degenerate" not in ignore:
        if scan["degenerate"]["tris"]["count"] > baseline["degenerate"]["tris"]["count"]:
            return False
    if int(scan["orphan_mopp"]) > int(baseline["orphan_mopp"]):
        return False
    if scan["orphan_collisions"] > baseline["orphan_collisions"]:
        return False
    if baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        if scan["holes"]["count"] > baseline["holes"]["count"] * 1.25 + 10:
            return False
        if scan["invisible_walls"]["count"] > baseline["invisible_walls"]["count"]:
            return False
    return True


def simplify_scan_is_acceptable(baseline: dict, scan: dict) -> bool:
    verdict = scan["verdict"].upper()
    if (
        scan["status"] == "BROKEN"
        or any(word in verdict for word in ("HEAVY", "CRASH", "HANG"))
        or scan["broken"]["refs"] != 0
        or scan["freeze"]["cullVerdict"] >= 1
    ):
        return False
    if scan["orientation"]["inverted"] > baseline["orientation"]["inverted"]:
        return False
    if scan["winding_cull"]["inverted"] > baseline["winding_cull"]["inverted"]:
        return False
    if scan["degenerate"]["tris"]["count"] > baseline["degenerate"]["tris"]["count"]:
        return False
    if baseline["ray_status"] == "ok" and scan["ray_status"] == "ok":
        if scan["holes"]["count"] > baseline["holes"]["count"] * 1.25 + 10:
            return False
        if scan["invisible_walls"]["count"] > baseline["invisible_walls"]["count"]:
            return False
    return True
