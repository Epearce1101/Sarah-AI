"""Where Sarah goes when Zero asks her (in desktop pet mode) to get out of
the way. Pure geometry, in physical screen pixels; desktop.py gathers the
real monitors and windows and the app moves the pet window.

- The app Zero is using is fullscreen: she goes to another monitor.
- It's in a window: she goes to the gap beside it on that screen (the
  biggest free space left, right, below or above it, avoiding other
  windows where she can).
- No gap (a maximized window, say): another monitor if there is one,
  otherwise the corner where she covers the least.

Rects are (left, top, right, bottom).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

Rect = Tuple[int, int, int, int]

MARGIN = 12  # pixels (at 100 %) between her and a window or screen edge


def area(r: Rect) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def overlap(a: Rect, b: Rect) -> int:
    return area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def covers(outer: Rect, inner: Rect, slack: int = 2) -> bool:
    return (outer[0] <= inner[0] + slack and outer[1] <= inner[1] + slack
            and outer[2] >= inner[2] - slack and outer[3] >= inner[3] - slack)


def monitor_of(r: Rect, monitors: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The monitor showing most of `r`."""
    best = max(monitors, key=lambda m: overlap(m["rect"], r), default=None)
    return best if best is not None and overlap(best["rect"], r) > 0 else None


def is_fullscreen(window: Rect, monitors: Sequence[Dict[str, Any]]) -> bool:
    mon = monitor_of(window, monitors)
    return bool(mon) and covers(window, mon["rect"])


def _size_on(mon: Dict[str, Any], pet_dip: Tuple[int, int]) -> Tuple[int, int]:
    s = float(mon.get("scale") or 1.0)
    return int(round(pet_dip[0] * s)), int(round(pet_dip[1] * s))


def _gap_spots(app: Rect, mon: Dict[str, Any], pet_dip: Tuple[int, int]) -> List[Tuple[Rect, int]]:
    """Spots in the gaps around `app` on its monitor where she fits whole,
    each with the size of its gap. She stands on the bottom of the work
    area where she can (as she does by default)."""
    w, h = _size_on(mon, pet_dip)
    m = int(MARGIN * float(mon.get("scale") or 1.0))
    wl, wt, wr, wb = mon["work"]
    al, at, ar, ab = app
    spots: List[Tuple[Rect, int]] = []
    right, left = wr - max(ar, wl), min(al, wr) - wl
    below, above = wb - max(ab, wt), min(at, wb) - wt
    if right >= w + 2 * m:
        x = max(ar, wl) + (right - w) // 2
        spots.append(((x, wb - h, x + w, wb), right * (wb - wt)))
    if left >= w + 2 * m:
        x = wl + (left - w) // 2
        spots.append(((x, wb - h, x + w, wb), left * (wb - wt)))
    if below >= h + m:
        for x in (wr - w - m, wl + m):
            spots.append(((x, wb - h, x + w, wb), below * (wr - wl)))
    if above >= h + m:
        for x in (wr - w - m, wl + m):
            spots.append(((x, wt + m, x + w, wt + m + h), above * (wr - wl)))
    return spots


def _corners(mon: Dict[str, Any], pet_dip: Tuple[int, int]) -> List[Rect]:
    w, h = _size_on(mon, pet_dip)
    m = int(MARGIN * float(mon.get("scale") or 1.0))
    wl, wt, wr, wb = mon["work"]
    return [(wr - w - m, wb - h, wr - m, wb), (wl + m, wb - h, wl + m + w, wb),
            (wr - w - m, wt + m, wr - m, wt + m + h), (wl + m, wt + m, wl + m + w, wt + m + h)]


def _covered(spot: Rect, windows: Sequence[Dict[str, Any]]) -> int:
    return sum(overlap(spot, w["rect"]) for w in windows)


def _other_monitor(app_mon: Dict[str, Any], pet: Rect, monitors: Sequence[Dict[str, Any]],
                   windows: Sequence[Dict[str, Any]], pet_dip: Tuple[int, int]) -> Optional[Rect]:
    others = [m for m in monitors if m is not app_mon]
    if not others:
        return None
    # The primary screen first, then the biggest.
    mon = sorted(others, key=lambda m: (not m.get("primary"), -area(m["rect"])))[0]
    return min(_corners(mon, pet_dip), key=lambda c: _covered(c, windows))


def plan(pet: Rect, pet_dip: Tuple[int, int], monitors: Sequence[Dict[str, Any]],
         windows: Sequence[Dict[str, Any]], app: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Where she should go. `windows` are the visible app windows, topmost
    first (not hers); `app` is the one Zero is using."""
    if not monitors:
        return {"move": False, "reason": "I can't see the screens"}
    if app is None:
        return {"move": False, "reason": "there's nothing open that I'm in the way of"}
    name = app.get("title") or app.get("app") or "that window"
    rect = app["rect"]
    app_mon = monitor_of(rect, monitors) or monitors[0]
    pet_mon = monitor_of(pet, monitors)
    if is_fullscreen(rect, monitors):
        if pet_mon is not None and pet_mon is not app_mon:
            return {"move": False, "reason": f"I'm already on the other screen, away from {name} (fullscreen)"}
        spot = _other_monitor(app_mon, pet, monitors, windows, pet_dip)
        if spot is not None:
            return {"move": True, "spot": spot, "reason": f"{name} is fullscreen, so I went to your other monitor"}
        spot = min(_corners(app_mon, pet_dip), key=lambda c: overlap(c, rect))
        return {"move": True, "spot": spot,
                "reason": f"{name} is fullscreen and there's no other monitor, so I tucked into the corner"}
    if pet_mon is not None and overlap(pet, rect) == 0:
        return {"move": False, "reason": f"I'm already clear of {name}"}
    others = [w for w in windows if w is not app and w["rect"] != rect]
    spots = _gap_spots(rect, app_mon, pet_dip)
    if spots:
        cx, cy = (pet[0] + pet[2]) / 2, (pet[1] + pet[3]) / 2
        best = min(spots, key=lambda s: (_covered(s[0], others), -s[1],
                                         abs((s[0][0] + s[0][2]) / 2 - cx) + abs((s[0][1] + s[0][3]) / 2 - cy)))
        return {"move": True, "spot": best[0], "reason": f"I moved into the gap beside {name}"}
    spot = _other_monitor(app_mon, pet, monitors, windows, pet_dip)
    if spot is not None:
        return {"move": True, "spot": spot, "reason": f"there's no gap beside {name}, so I went to your other monitor"}
    spot = min(_corners(app_mon, pet_dip), key=lambda c: overlap(c, rect))
    return {"move": True, "spot": spot, "reason": f"there's no gap beside {name}, so I moved to the corner where I cover the least"}
