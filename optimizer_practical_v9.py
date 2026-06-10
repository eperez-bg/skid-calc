from __future__ import annotations

from dataclasses import dataclass
import math
import time

from models import (
    Carton,
    Orientation,
    Item,
    Placement,
    Layer,
    SkidPlan,
)



# ============================================================
# RUNTIME TIMER
# ============================================================

PROGRAM_START_TIME = time.perf_counter()


def elapsed_seconds() -> float:
    """
    Returns total seconds since the optimizer module was loaded.

    This is close to total program runtime because main imports the Excel utils,
    which import this optimizer module before processing begins.
    """

    return time.perf_counter() - PROGRAM_START_TIME


def format_seconds(seconds: float) -> str:
    """
    Formats seconds as mm:ss.xx for console output.
    """

    minutes = int(seconds // 60)
    remaining_seconds = seconds - (minutes * 60)

    return f"{minutes:02d}:{remaining_seconds:05.2f}"


def print_elapsed(message: str, attempt_start_time: float | None = None) -> None:
    """
    Prints a message with total elapsed runtime.

    If attempt_start_time is provided, also prints how long that specific
    attempt took.
    """

    total = elapsed_seconds()

    if attempt_start_time is None:
        print(f"[elapsed {format_seconds(total)}] {message}")
    else:
        attempt_seconds = time.perf_counter() - attempt_start_time
        print(
            f"[elapsed {format_seconds(total)} | attempt {format_seconds(attempt_seconds)}] "
            f"{message}"
        )



# ============================================================
# PHYSICAL CONSTANTS
# ============================================================

MAX_LOADED_HEIGHT = 87.52
MAX_SKID_WIDTH = 90.5

# Keep this to prevent absurdly long skids.
# Set to None if you truly want unlimited skid length.
MAX_SKID_LENGTH = 96.0

LENGTH_CLEARANCE = 1.97
WIDTH_CLEARANCE = 1.18

HEIGHT_CLEARANCE = 1.0  # output skid height = actual packed carton height + this clearance

WIDTH_STEP = 0.5
EPS = 1e-9


# ============================================================
# SUPPORT / PACKING SETTINGS
# ============================================================

# Higher = stricter support.
MIN_SUPPORT_RATIO = 0.90

# Maximum side overhang allowed.
MAX_SIDE_OVERHANG = 1.0

# Cartons can share a layer if their heights are close enough.
HEIGHT_MATCH_TOLERANCE = 0.75

# A carton can support the layer above if it is close enough
# to the top of its layer.
SUPPORT_HEIGHT_TOLERANCE = 0.75

# Scoring weights.
OVERHANG_SCORE_WEIGHT = 3000.0
SUPPORT_WASTE_WEIGHT = 0.10
CENTER_OFFSET_WEIGHT = 20.0
LAYER_BALANCE_WEIGHT = 35.0
HEIGHT_WEIGHT = 75.0

# Print diagnostic information when a group cannot produce a valid skid.
DEBUG_NO_VALID_SKID = True


# ============================================================
# GEOMETRY MODELS
# ============================================================

@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    length: float
    width: float

    @property
    def x2(self) -> float:
        return self.x + self.length

    @property
    def y2(self) -> float:
        return self.y + self.width

    @property
    def area(self) -> float:
        return self.length * self.width


@dataclass(frozen=True)
class CandidatePosition:
    x: float
    y: float
    new_layer_length: float
    new_layer_width: float
    support_ratio: float
    side_overhang: float
    center_offset: float
    support_waste: float
    layer_balance_offset: float


# ============================================================
# ORIENTATION LOGIC
# ============================================================

def get_orientations(carton: Carton):
    """
    Flat rotation only.

    Allowed:
        L x W x H
        W x L x H

    Not allowed:
        Standing cartons upright.
    """

    yield Orientation(
        length=carton.length,
        width=carton.width,
        height=carton.height,
        label="Flat: L along skid length, W across skid width, H vertical",
    )

    if abs(carton.length - carton.width) > EPS:
        yield Orientation(
            length=carton.width,
            width=carton.length,
            height=carton.height,
            label="Flat rotated: W along skid length, L across skid width, H vertical",
        )


# ============================================================
# MEASUREMENT HELPERS
# ============================================================

def layer_used_length(layer: Layer) -> float:
    if not layer.placements:
        return 0.0

    return max(p.x + p.length for p in layer.placements)


def layer_used_width(layer: Layer) -> float:
    if not layer.placements:
        return 0.0

    return max(p.y + p.width for p in layer.placements)


def current_internal_length(layers: list[Layer]) -> float:
    if not layers:
        return 0.0

    return max(layer_used_length(layer) for layer in layers)


def current_internal_width(layers: list[Layer]) -> float:
    if not layers:
        return 0.0

    return max(layer_used_width(layer) for layer in layers)


def current_actual_height(layers: list[Layer]) -> float:
    return sum(layer.height for layer in layers)


def final_skid_length(internal_length: float) -> float:
    return internal_length + LENGTH_CLEARANCE


def final_skid_width(internal_width: float) -> float:
    return internal_width + WIDTH_CLEARANCE


def max_internal_width() -> float:
    return MAX_SKID_WIDTH - WIDTH_CLEARANCE


def max_internal_length() -> float | None:
    if MAX_SKID_LENGTH is None:
        return None

    return MAX_SKID_LENGTH - LENGTH_CLEARANCE


def is_within_skid_limits(skid_length: float, skid_width: float) -> bool:
    if skid_width > MAX_SKID_WIDTH + EPS:
        return False

    if MAX_SKID_LENGTH is not None and skid_length > MAX_SKID_LENGTH + EPS:
        return False

    return True


# ============================================================
# COLLISION HELPERS
# ============================================================

def rectangles_overlap(
    x: float,
    y: float,
    length: float,
    width: float,
    placement: Placement,
) -> bool:
    """
    Checks whether a candidate carton overlaps an existing carton
    in the same layer.

    Touching edges is okay.
    """

    return not (
        x + length <= placement.x + EPS or
        placement.x + placement.length <= x + EPS or
        y + width <= placement.y + EPS or
        placement.y + placement.width <= y + EPS
    )


def has_collision(
    layer: Layer,
    x: float,
    y: float,
    length: float,
    width: float,
) -> bool:
    for placement in layer.placements:
        if rectangles_overlap(x, y, length, width, placement):
            return True

    return False


# ============================================================
# SUPPORT HELPERS
# ============================================================

def intersection_rect(a: Rect, b: Rect) -> tuple[float, float, float, float] | None:
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)

    if x2 <= x1 + EPS or y2 <= y1 + EPS:
        return None

    return x1, y1, x2, y2


def union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """
    Calculates union area so overlapping support zones are not double-counted.
    """

    if not rects:
        return 0.0

    xs = sorted(set([r[0] for r in rects] + [r[2] for r in rects]))

    total_area = 0.0

    for i in range(len(xs) - 1):
        x_left = xs[i]
        x_right = xs[i + 1]

        if x_right <= x_left + EPS:
            continue

        intervals = []

        for rx1, ry1, rx2, ry2 in rects:
            if rx1 <= x_left + EPS and rx2 >= x_right - EPS:
                intervals.append((ry1, ry2))

        if not intervals:
            continue

        intervals.sort()

        merged = []
        start, end = intervals[0]

        for next_start, next_end in intervals[1:]:
            if next_start <= end + EPS:
                end = max(end, next_end)
            else:
                merged.append((start, end))
                start, end = next_start, next_end

        merged.append((start, end))

        covered_y = sum(end - start for start, end in merged)
        total_area += (x_right - x_left) * covered_y

    return total_area


def get_support_zones_from_layer(layer: Layer) -> list[Rect]:
    """
    Cartons in the layer below become support zones for the layer above.

    A carton can support the layer above if it is close enough to the
    top of the layer. This helps when similar-height cartons are grouped
    together, like 3.15 and 3.35 inch cartons.
    """

    zones = []

    for p in layer.placements:
        if p.height >= layer.height - SUPPORT_HEIGHT_TOLERANCE - EPS:
            zones.append(
                Rect(
                    x=p.x,
                    y=p.y,
                    length=p.length,
                    width=p.width,
                )
            )

    return zones


def check_support(
    x: float,
    y: float,
    length: float,
    width: float,
    support_zones: list[Rect] | None,
) -> tuple[bool, float, float]:
    """
    Bottom layer is always supported by the skid.

    Upper layers must be supported by cartons underneath.
    """

    if support_zones is None:
        return True, 1.0, 0.0

    top_rect = Rect(x=x, y=y, length=length, width=width)

    intersections = []
    touching_zones = []

    for zone in support_zones:
        intersection = intersection_rect(top_rect, zone)

        if intersection is not None:
            intersections.append(intersection)
            touching_zones.append(zone)

    if not intersections:
        return False, 0.0, float("inf")

    supported_area = union_area(intersections)
    support_ratio = supported_area / top_rect.area

    support_min_x = min(zone.x for zone in touching_zones)
    support_max_x = max(zone.x2 for zone in touching_zones)
    support_min_y = min(zone.y for zone in touching_zones)
    support_max_y = max(zone.y2 for zone in touching_zones)

    left_overhang = max(0.0, support_min_x - top_rect.x)
    right_overhang = max(0.0, top_rect.x2 - support_max_x)
    front_overhang = max(0.0, support_min_y - top_rect.y)
    back_overhang = max(0.0, top_rect.y2 - support_max_y)

    side_overhang = max(
        left_overhang,
        right_overhang,
        front_overhang,
        back_overhang,
    )

    valid = (
        support_ratio >= MIN_SUPPORT_RATIO - EPS
        and side_overhang <= MAX_SIDE_OVERHANG + EPS
    )

    return valid, support_ratio, side_overhang


def support_center_offset(
    x: float,
    y: float,
    length: float,
    width: float,
    support_zones: list[Rect] | None,
) -> float:
    """
    Measures how centered the carton is on the support zones it touches.

    Lower is better.
    """

    if support_zones is None:
        return 0.0

    top_rect = Rect(x=x, y=y, length=length, width=width)

    touching_zones = []

    for zone in support_zones:
        if intersection_rect(top_rect, zone) is not None:
            touching_zones.append(zone)

    if not touching_zones:
        return float("inf")

    min_x = min(zone.x for zone in touching_zones)
    max_x = max(zone.x2 for zone in touching_zones)
    min_y = min(zone.y for zone in touching_zones)
    max_y = max(zone.y2 for zone in touching_zones)

    support_center_x = (min_x + max_x) / 2
    support_center_y = (min_y + max_y) / 2

    carton_center_x = x + length / 2
    carton_center_y = y + width / 2

    return (
        abs(carton_center_x - support_center_x)
        + abs(carton_center_y - support_center_y)
    )


def support_waste(
    x: float,
    y: float,
    length: float,
    width: float,
    support_zones: list[Rect] | None,
) -> float:
    """
    Penalizes putting a small carton on a much larger support area.

    Lower is better.
    """

    if support_zones is None:
        return 0.0

    top_rect = Rect(x=x, y=y, length=length, width=width)

    touching_zones = []

    for zone in support_zones:
        if intersection_rect(top_rect, zone) is not None:
            touching_zones.append(zone)

    if not touching_zones:
        return float("inf")

    min_x = min(zone.x for zone in touching_zones)
    max_x = max(zone.x2 for zone in touching_zones)
    min_y = min(zone.y for zone in touching_zones)
    max_y = max(zone.y2 for zone in touching_zones)

    support_bbox_area = (max_x - min_x) * (max_y - min_y)

    return max(0.0, support_bbox_area - top_rect.area)


def layer_balance_offset(
    layer: Layer,
    x: float,
    y: float,
    length: float,
    width: float,
) -> float:
    """
    Measures how well this candidate stays grouped with the rest of the layer.

    Lower is better.

    This helps stop a small sub-stack from sitting too far off to one side
    when it could move inward and keep the center of gravity closer to the middle.
    """

    carton_center_x = x + length / 2
    carton_center_y = y + width / 2

    if not layer.placements:
        return 0.0

    min_x = min([p.x for p in layer.placements] + [x])
    max_x = max([p.x + p.length for p in layer.placements] + [x + length])

    min_y = min([p.y for p in layer.placements] + [y])
    max_y = max([p.y + p.width for p in layer.placements] + [y + width])

    combined_center_x = (min_x + max_x) / 2
    combined_center_y = (min_y + max_y) / 2

    return (
        abs(carton_center_x - combined_center_x)
        + abs(carton_center_y - combined_center_y)
    )


# ============================================================
# POSITION GENERATION
# ============================================================

def candidate_xy_points(
    layer: Layer,
    orientation: Orientation,
    support_zones: list[Rect] | None,
) -> set[tuple[float, float]]:
    """
    Generates useful x/y candidate points.

    It uses:
    - origin
    - existing carton edges
    - centered positions
    - support zone edges
    - support zone centers
    """

    xs = {0.0}
    ys = {0.0}

    for p in layer.placements:
        xs.add(p.x)
        ys.add(p.y)

        xs.add(p.x + p.length)
        ys.add(p.y + p.width)

        xs.add(p.x + p.length - orientation.length)
        ys.add(p.y + p.width - orientation.width)

        xs.add(p.x + (p.length - orientation.length) / 2)
        ys.add(p.y + (p.width - orientation.width) / 2)

    if support_zones is not None:
        support_min_x = min(zone.x for zone in support_zones)
        support_max_x = max(zone.x2 for zone in support_zones)
        support_min_y = min(zone.y for zone in support_zones)
        support_max_y = max(zone.y2 for zone in support_zones)

        xs.add(support_min_x)
        ys.add(support_min_y)

        xs.add(support_max_x - orientation.length)
        ys.add(support_max_y - orientation.width)

        xs.add(
            support_min_x
            + ((support_max_x - support_min_x) - orientation.length) / 2
        )
        ys.add(
            support_min_y
            + ((support_max_y - support_min_y) - orientation.width) / 2
        )

        for zone in support_zones:
            xs.add(zone.x)
            ys.add(zone.y)

            xs.add(zone.x2 - orientation.length)
            ys.add(zone.y2 - orientation.width)

            xs.add(zone.x + (zone.length - orientation.length) / 2)
            ys.add(zone.y + (zone.width - orientation.width) / 2)

            xs.add(zone.x - MAX_SIDE_OVERHANG)
            ys.add(zone.y - MAX_SIDE_OVERHANG)

            xs.add(zone.x2 - orientation.length + MAX_SIDE_OVERHANG)
            ys.add(zone.y2 - orientation.width + MAX_SIDE_OVERHANG)

    points = set()

    for x in xs:
        for y in ys:
            if x >= -EPS and y >= -EPS:
                points.add((round(max(0.0, x), 4), round(max(0.0, y), 4)))

    return points


def layer_height_compatible(layer: Layer, orientation: Orientation) -> bool:
    """
    Allows cartons with close heights to share the same layer.

    Important:
    This allows a slightly taller carton to join a slightly shorter layer.
    The layer height will be expanded later if needed.
    """

    return abs(layer.height - orientation.height) <= HEIGHT_MATCH_TOLERANCE + EPS


def find_best_position_in_layer(
    layer: Layer,
    orientation: Orientation,
    width_limit: float,
    length_limit: float | None,
    support_zones: list[Rect] | None,
) -> CandidatePosition | None:
    if not layer_height_compatible(layer, orientation):
        return None

    best_position = None
    best_score = None

    for x, y in candidate_xy_points(layer, orientation, support_zones):
        if y + orientation.width > width_limit + EPS:
            continue

        if length_limit is not None and x + orientation.length > length_limit + EPS:
            continue

        if has_collision(layer, x, y, orientation.length, orientation.width):
            continue

        support_valid, support_ratio, side_overhang = check_support(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        if not support_valid:
            continue

        center_offset = support_center_offset(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        waste = support_waste(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        balance_offset = layer_balance_offset(
            layer=layer,
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
        )

        new_layer_length = max(layer_used_length(layer), x + orientation.length)
        new_layer_width = max(layer_used_width(layer), y + orientation.width)

        position = CandidatePosition(
            x=x,
            y=y,
            new_layer_length=new_layer_length,
            new_layer_width=new_layer_width,
            support_ratio=support_ratio,
            side_overhang=side_overhang,
            center_offset=center_offset,
            support_waste=waste,
            layer_balance_offset=balance_offset,
        )

        score = (
            side_overhang * OVERHANG_SCORE_WEIGHT,
            -support_ratio,
            waste * SUPPORT_WASTE_WEIGHT,
            center_offset * CENTER_OFFSET_WEIGHT,
            balance_offset * LAYER_BALANCE_WEIGHT,
            new_layer_length * new_layer_width,
            new_layer_length,
            new_layer_width,
        )

        if best_score is None or score < best_score:
            best_score = score
            best_position = position

    return best_position


# ============================================================
# PLACEMENT
# ============================================================

def place_item_in_layer(
    layer: Layer,
    item: Item,
    orientation: Orientation,
    position: CandidatePosition,
    layer_number: int,
) -> None:
    layer.placements.append(
        Placement(
            csv_row_number=item.csv_row_number,
            copy_number=item.copy_number,
            x=position.x,
            y=position.y,
            z=layer.z,
            length=orientation.length,
            width=orientation.width,
            height=orientation.height,
            orientation=orientation.label,
            layer_number=layer_number,
        )
    )


def grow_layer_height_if_needed(
    layers: list[Layer],
    layer_index: int,
    new_height: float,
) -> None:
    """
    Expands a layer height if a slightly taller carton is added to it.

    If the layer grows, all layers above it must move upward by the same
    amount so z coordinates remain correct.
    """

    layer = layers[layer_index]

    if new_height <= layer.height + EPS:
        return

    height_delta = new_height - layer.height
    layer.height = new_height

    for upper_index in range(layer_index + 1, len(layers)):
        upper_layer = layers[upper_index]
        shifted_placements = []

        for p in upper_layer.placements:
            shifted_placements.append(
                Placement(
                    csv_row_number=p.csv_row_number,
                    copy_number=p.copy_number,
                    x=p.x,
                    y=p.y,
                    z=p.z + height_delta,
                    length=p.length,
                    width=p.width,
                    height=p.height,
                    orientation=p.orientation,
                    layer_number=p.layer_number,
                )
            )

        layers[upper_index] = Layer(
            z=upper_layer.z + height_delta,
            height=upper_layer.height,
            placements=shifted_placements,
        )


# ============================================================
# VALIDATION / CENTERING
# ============================================================

def get_layer_bounds(layer: Layer) -> tuple[float, float, float, float] | None:
    if not layer.placements:
        return None

    min_x = min(p.x for p in layer.placements)
    max_x = max(p.x + p.length for p in layer.placements)
    min_y = min(p.y for p in layer.placements)
    max_y = max(p.y + p.width for p in layer.placements)

    return min_x, max_x, min_y, max_y


def get_plan_bounds(plan: SkidPlan) -> tuple[float, float, float, float] | None:
    if not plan.placements:
        return None

    min_x = min(p.x for p in plan.placements)
    max_x = max(p.x + p.length for p in plan.placements)
    min_y = min(p.y for p in plan.placements)
    max_y = max(p.y + p.width for p in plan.placements)

    return min_x, max_x, min_y, max_y


def shift_layer(layer: Layer, dx: float, dy: float) -> Layer:
    shifted_placements = []

    for p in layer.placements:
        shifted_placements.append(
            Placement(
                csv_row_number=p.csv_row_number,
                copy_number=p.copy_number,
                x=p.x + dx,
                y=p.y + dy,
                z=p.z,
                length=p.length,
                width=p.width,
                height=p.height,
                orientation=p.orientation,
                layer_number=p.layer_number,
            )
        )

    return Layer(
        z=layer.z,
        height=layer.height,
        placements=shifted_placements,
    )


def shift_layers_from_index(
    layers: list[Layer],
    start_index: int,
    dx: float,
    dy: float,
) -> list[Layer]:
    shifted_layers = []

    for index, layer in enumerate(layers):
        if index >= start_index:
            shifted_layers.append(shift_layer(layer, dx, dy))
        else:
            shifted_layers.append(layer)

    return shifted_layers


def layer_has_collisions(layer: Layer) -> bool:
    placements = layer.placements

    for i in range(len(placements)):
        a = placements[i]

        for j in range(i + 1, len(placements)):
            b = placements[j]

            if rectangles_overlap(a.x, a.y, a.length, a.width, b):
                return True

    return False


def plan_is_valid(plan: SkidPlan) -> bool:
    for layer in plan.layers:
        if layer_has_collisions(layer):
            return False

        for p in layer.placements:
            if p.x < -EPS or p.y < -EPS:
                return False

            if p.x + p.length > plan.skid_length + EPS:
                return False

            if p.y + p.width > plan.skid_width + EPS:
                return False

    for layer_index in range(1, len(plan.layers)):
        support_zones = get_support_zones_from_layer(plan.layers[layer_index - 1])

        if not support_zones:
            return False

        for p in plan.layers[layer_index].placements:
            valid, _, _ = check_support(
                x=p.x,
                y=p.y,
                length=p.length,
                width=p.width,
                support_zones=support_zones,
            )

            if not valid:
                return False

    return True


def rebuild_plan_with_layers(plan: SkidPlan, layers: list[Layer]) -> SkidPlan:
    return SkidPlan(
        skid_length=plan.skid_length,
        skid_width=plan.skid_width,
        skid_height=plan.skid_height,
        layers=layers,
    )


def center_whole_plan_on_skid(plan: SkidPlan) -> SkidPlan:
    bounds = get_plan_bounds(plan)

    if bounds is None:
        return plan

    min_x, max_x, min_y, max_y = bounds

    used_length = max_x - min_x
    used_width = max_y - min_y

    target_min_x = (plan.skid_length - used_length) / 2
    target_min_y = (plan.skid_width - used_width) / 2

    dx = target_min_x - min_x
    dy = target_min_y - min_y

    shifted_layers = shift_layers_from_index(
        layers=plan.layers,
        start_index=0,
        dx=dx,
        dy=dy,
    )

    centered_plan = rebuild_plan_with_layers(plan, shifted_layers)

    if plan_is_valid(centered_plan):
        return centered_plan

    return plan


def center_upper_layers_safely(plan: SkidPlan) -> SkidPlan:
    current_plan = plan

    for layer_index in range(1, len(current_plan.layers)):
        below_bounds = get_layer_bounds(current_plan.layers[layer_index - 1])
        current_bounds = get_layer_bounds(current_plan.layers[layer_index])

        if below_bounds is None or current_bounds is None:
            continue

        below_min_x, below_max_x, below_min_y, below_max_y = below_bounds
        current_min_x, current_max_x, current_min_y, current_max_y = current_bounds

        below_length = below_max_x - below_min_x
        below_width = below_max_y - below_min_y

        current_length = current_max_x - current_min_x
        current_width = current_max_y - current_min_y

        target_min_x = below_min_x + (below_length - current_length) / 2
        target_min_y = below_min_y + (below_width - current_width) / 2

        dx = target_min_x - current_min_x
        dy = target_min_y - current_min_y

        shifted_layers = shift_layers_from_index(
            layers=current_plan.layers,
            start_index=layer_index,
            dx=dx,
            dy=dy,
        )

        candidate_plan = rebuild_plan_with_layers(current_plan, shifted_layers)

        if plan_is_valid(candidate_plan):
            current_plan = candidate_plan

    return current_plan


def center_plan_on_skid(plan: SkidPlan) -> SkidPlan:
    centered = center_whole_plan_on_skid(plan)
    centered = center_upper_layers_safely(centered)

    return centered


# ============================================================
# PACKING
# ============================================================

def packing_choice_score(
    final_length_value: float,
    final_width_value: float,
    actual_height: float,
    position: CandidatePosition,
    layer_index: int,
) -> tuple:
    area = final_length_value * final_width_value

    return (
        area,
        actual_height * HEIGHT_WEIGHT,
        position.side_overhang * OVERHANG_SCORE_WEIGHT,
        max(0.0, 1.0 - position.support_ratio) * OVERHANG_SCORE_WEIGHT,
        position.support_waste * SUPPORT_WASTE_WEIGHT,
        position.center_offset * CENTER_OFFSET_WEIGHT,
        position.layer_balance_offset * LAYER_BALANCE_WEIGHT,
        -layer_index,  # prefer higher existing layers when otherwise similar
        final_length_value,
        final_width_value,
    )


def make_layer(z: float, height: float) -> Layer:
    return Layer(
        z=z,
        height=height,
        placements=[],
    )


def pack_items_for_width(
    items: list[Item],
    width_limit: float,
    length_limit: float | None,
) -> SkidPlan | None:
    layers: list[Layer] = []

    sorted_items = sorted(
        items,
        key=lambda item: (
            item.carton.length * item.carton.width,
            max(item.carton.length, item.carton.width),
            item.carton.length + item.carton.width,
            item.carton.height,
        ),
        reverse=True,
    )

    for item in sorted_items:
        best_choice = None
        best_score = None

        current_length = current_internal_length(layers)
        current_width = current_internal_width(layers)
        current_height = current_actual_height(layers)

        for orientation in get_orientations(item.carton):
            if orientation.width > width_limit + EPS:
                continue

            if length_limit is not None and orientation.length > length_limit + EPS:
                continue

            if orientation.height > MAX_LOADED_HEIGHT + EPS:
                continue

            # Try existing layers.
            for layer_index, layer in enumerate(layers):

                # Allow a similar-height carton to join any compatible layer.
                # If it is slightly taller, grow_layer_height_if_needed()
                # will raise the layers above it so z coordinates stay correct.
                #
                # This matters for cases like a 1.97" carton joining a 1.77"
                # layer instead of being forced into a bad upper stack.
                if layer_index == 0:
                    support_zones = None
                else:
                    support_zones = get_support_zones_from_layer(layers[layer_index - 1])

                    if not support_zones:
                        continue

                position = find_best_position_in_layer(
                    layer=layer,
                    orientation=orientation,
                    width_limit=width_limit,
                    length_limit=length_limit,
                    support_zones=support_zones,
                )

                if position is None:
                    continue

                new_internal_length = max(current_length, position.new_layer_length)
                new_internal_width = max(current_width, position.new_layer_width)

                height_delta = max(0.0, orientation.height - layer.height)
                new_actual_height = current_height + height_delta

                skid_l = final_skid_length(new_internal_length)
                skid_w = final_skid_width(new_internal_width)

                if not is_within_skid_limits(skid_l, skid_w):
                    continue

                if new_actual_height > MAX_LOADED_HEIGHT + EPS:
                    continue

                score = packing_choice_score(
                    final_length_value=skid_l,
                    final_width_value=skid_w,
                    actual_height=new_actual_height,
                    position=position,
                    layer_index=layer_index,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_choice = (
                        "existing",
                        layer_index,
                        orientation,
                        position,
                    )

            # Try new layer.
            new_actual_height = current_height + orientation.height

            if new_actual_height <= MAX_LOADED_HEIGHT + EPS:
                if not layers:
                    support_zones = None
                    z = 0.0
                else:
                    support_zones = get_support_zones_from_layer(layers[-1])
                    z = current_height

                    if not support_zones:
                        continue

                new_layer = make_layer(
                    z=z,
                    height=orientation.height,
                )

                position = find_best_position_in_layer(
                    layer=new_layer,
                    orientation=orientation,
                    width_limit=width_limit,
                    length_limit=length_limit,
                    support_zones=support_zones,
                )

                if position is None:
                    continue

                new_internal_length = max(current_length, position.new_layer_length)
                new_internal_width = max(current_width, position.new_layer_width)

                skid_l = final_skid_length(new_internal_length)
                skid_w = final_skid_width(new_internal_width)

                if not is_within_skid_limits(skid_l, skid_w):
                    continue

                score = packing_choice_score(
                    final_length_value=skid_l,
                    final_width_value=skid_w,
                    actual_height=new_actual_height,
                    position=position,
                    layer_index=len(layers),
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_choice = (
                        "new",
                        None,
                        orientation,
                        position,
                    )

        if best_choice is None:
            return None

        choice_type, layer_index, orientation, position = best_choice

        if choice_type == "existing":
            layer = layers[layer_index]

            place_item_in_layer(
                layer=layer,
                item=item,
                orientation=orientation,
                position=position,
                layer_number=layer_index + 1,
            )

            grow_layer_height_if_needed(
                layers=layers,
                layer_index=layer_index,
                new_height=orientation.height,
            )

        else:
            z = current_actual_height(layers)

            new_layer = make_layer(
                z=z,
                height=orientation.height,
            )

            layers.append(new_layer)

            place_item_in_layer(
                layer=new_layer,
                item=item,
                orientation=orientation,
                position=position,
                layer_number=len(layers),
            )

    internal_length = current_internal_length(layers)
    internal_width = current_internal_width(layers)
    actual_height = current_actual_height(layers)

    skid_l = final_skid_length(internal_length)
    skid_w = final_skid_width(internal_width)
    skid_h = actual_height + HEIGHT_CLEARANCE

    if not is_within_skid_limits(skid_l, skid_w):
        return None

    if actual_height > MAX_LOADED_HEIGHT + EPS:
        return None

    raw_plan = SkidPlan(
        skid_length=skid_l,
        skid_width=skid_w,
        skid_height=skid_h,
        layers=layers,
    )

    centered = center_plan_on_skid(raw_plan)

    if not plan_is_valid(centered):
        return None

    return centered


# ============================================================
# WIDTH CANDIDATES
# ============================================================

def generate_candidate_widths(items: list[Item]) -> list[float]:
    lower_bound = 0.0

    for item in items:
        possible_widths = [
            orientation.width
            for orientation in get_orientations(item.carton)
            if orientation.height <= MAX_LOADED_HEIGHT + EPS
        ]

        if not possible_widths:
            return []

        lower_bound = max(lower_bound, min(possible_widths))

    if lower_bound > max_internal_width() + EPS:
        return []

    widths = set()

    widths.add(round(max_internal_width(), 4))

    start = math.ceil(lower_bound / WIDTH_STEP) * WIDTH_STEP
    width = start

    while width <= max_internal_width() + EPS:
        widths.add(round(width, 4))
        width += WIDTH_STEP

    all_widths = []

    for item in items:
        for orientation in get_orientations(item.carton):
            if orientation.width <= max_internal_width() + EPS:
                widths.add(round(orientation.width, 4))
                all_widths.append(orientation.width)

    # Try common 2-carton width combinations.
    for a in all_widths:
        for b in all_widths:
            total = a + b

            if total <= max_internal_width() + EPS:
                widths.add(round(total, 4))

    return sorted(widths)




# ============================================================
# FALLBACK STACKING
# ============================================================

def choose_fallback_base_and_orientations(
    items: list[Item],
) -> tuple[float, float, dict[int, Orientation]] | None:
    """
    Finds a simple base footprint that every carton can fit inside.

    This is used only as a fallback when the layered greedy optimizer cannot
    find a plan. It prevents a group from returning NO VALID SKID when a simple
    centered vertical stack would work.

    It tries candidate base lengths and widths built from the allowed flat
    orientations, then chooses the smallest valid footprint.
    """

    length_limit = max_internal_length()
    width_limit = max_internal_width()

    orientations_by_index: dict[int, list[Orientation]] = {}

    candidate_lengths = set()
    candidate_widths = set()

    for index, item in enumerate(items):
        valid_orientations = []

        for orientation in get_orientations(item.carton):
            if orientation.width > width_limit + EPS:
                continue

            if length_limit is not None and orientation.length > length_limit + EPS:
                continue

            valid_orientations.append(orientation)
            candidate_lengths.add(round(orientation.length, 4))
            candidate_widths.add(round(orientation.width, 4))

        if not valid_orientations:
            return None

        orientations_by_index[index] = valid_orientations

    best = None
    best_score = None

    for base_length in candidate_lengths:
        for base_width in candidate_widths:
            if base_width > width_limit + EPS:
                continue

            if length_limit is not None and base_length > length_limit + EPS:
                continue

            chosen_orientations: dict[int, Orientation] = {}

            for index, valid_orientations in orientations_by_index.items():
                fitting = [
                    orientation
                    for orientation in valid_orientations
                    if (
                        orientation.length <= base_length + EPS
                        and orientation.width <= base_width + EPS
                    )
                ]

                if not fitting:
                    break

                # Prefer the orientation that most closely matches the selected base.
                chosen_orientations[index] = min(
                    fitting,
                    key=lambda orientation: (
                        (base_length - orientation.length)
                        + (base_width - orientation.width),
                        abs(base_length - orientation.length),
                        abs(base_width - orientation.width),
                    ),
                )
            else:
                skid_l = final_skid_length(base_length)
                skid_w = final_skid_width(base_width)

                if not is_within_skid_limits(skid_l, skid_w):
                    continue

                score = (
                    skid_l * skid_w,
                    skid_l,
                    skid_w,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best = (base_length, base_width, chosen_orientations)

    return best


def fallback_centered_stack_plan(items: list[Item]) -> SkidPlan | None:
    """
    Conservative fallback plan.

    If the main optimizer cannot find a more complex layered arrangement, this
    stacks cartons one per layer, centered on each other, largest footprint first.

    This is not always the most efficient layout, but it is safe and should
    prevent avoidable NO VALID SKID results for groups that can be stacked.
    """

    if not items:
        return None

    base_result = choose_fallback_base_and_orientations(items)

    if base_result is None:
        return None

    base_length, base_width, chosen_orientations = base_result

    indexed_items = list(enumerate(items))

    # Largest footprints first so smaller cartons sit on larger cartons.
    indexed_items.sort(
        key=lambda pair: (
            chosen_orientations[pair[0]].length * chosen_orientations[pair[0]].width,
            min(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
            max(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
        ),
        reverse=True,
    )

    layers: list[Layer] = []
    z = 0.0

    for layer_number, (item_index, item) in enumerate(indexed_items, start=1):
        orientation = chosen_orientations[item_index]

        x = (base_length - orientation.length) / 2
        y = (base_width - orientation.width) / 2

        layer = make_layer(
            z=z,
            height=orientation.height,
        )

        place_item_in_layer(
            layer=layer,
            item=item,
            orientation=orientation,
            position=CandidatePosition(
                x=x,
                y=y,
                new_layer_length=base_length,
                new_layer_width=base_width,
                support_ratio=1.0,
                side_overhang=0.0,
                center_offset=0.0,
                support_waste=0.0,
                layer_balance_offset=0.0,
            ),
            layer_number=layer_number,
        )

        layers.append(layer)
        z += orientation.height

    actual_height = current_actual_height(layers)

    if actual_height > MAX_LOADED_HEIGHT + EPS:
        return None

    plan = SkidPlan(
        skid_length=final_skid_length(base_length),
        skid_width=final_skid_width(base_width),
        skid_height=actual_height + HEIGHT_CLEARANCE,
        layers=layers,
    )

    centered = center_plan_on_skid(plan)

    if plan_is_valid(centered):
        return centered

    return None








# ============================================================
# COMPACT NON-CENTERED FALLBACK PACKING
# ============================================================

def generate_fallback_length_limits(items: list[Item]) -> list[float]:
    """
    Generates internal skid-length limits to try for compact fallback packing.
    """

    hard_limit = max_internal_length()

    if hard_limit is None:
        hard_limit = max(
            max(orientation.length for orientation in get_orientations(item.carton))
            for item in items
        )

    lengths = set()
    lengths.add(round(hard_limit, 4))

    all_lengths = []

    for item in items:
        for orientation in get_orientations(item.carton):
            if orientation.length <= hard_limit + EPS:
                lengths.add(round(orientation.length, 4))
                all_lengths.append(orientation.length)

    # Try useful two-carton length combinations.
    for a in all_lengths:
        for b in all_lengths:
            total = a + b

            if total <= hard_limit + EPS:
                lengths.add(round(total, 4))

    return sorted(lengths)


def candidate_xy_points_compact(
    layer: Layer,
    orientation: Orientation,
    support_zones: list[Rect] | None,
) -> set[tuple[float, float]]:
    """
    Candidate points for fallback packing.

    This intentionally includes more bottom-left / edge-aligned points and does
    not prefer centered points first. The goal is to keep usable open space on
    the support layer instead of splitting it into two unusable strips.
    """

    xs = {0.0}
    ys = {0.0}

    for p in layer.placements:
        xs.add(p.x)
        ys.add(p.y)

        xs.add(p.x + p.length)
        ys.add(p.y + p.width)

        xs.add(p.x + p.length - orientation.length)
        ys.add(p.y + p.width - orientation.width)

    if support_zones is not None:
        support_min_x = min(zone.x for zone in support_zones)
        support_max_x = max(zone.x2 for zone in support_zones)
        support_min_y = min(zone.y for zone in support_zones)
        support_max_y = max(zone.y2 for zone in support_zones)

        xs.add(support_min_x)
        xs.add(support_max_x - orientation.length)
        ys.add(support_min_y)
        ys.add(support_max_y - orientation.width)

        for zone in support_zones:
            xs.add(zone.x)
            xs.add(zone.x2 - orientation.length)
            xs.add(zone.x + (zone.length - orientation.length) / 2)

            ys.add(zone.y)
            ys.add(zone.y2 - orientation.width)
            ys.add(zone.y + (zone.width - orientation.width) / 2)

            # Also try just inside the support zone, useful for leaving clean
            # strips for other cartons.
            xs.add(zone.x + MAX_SIDE_OVERHANG)
            ys.add(zone.y + MAX_SIDE_OVERHANG)

    points = set()

    for x in xs:
        for y in ys:
            if x >= -EPS and y >= -EPS:
                points.add((round(max(0.0, x), 4), round(max(0.0, y), 4)))

    return points


def find_compact_fallback_position(
    layer: Layer,
    orientation: Orientation,
    width_limit: float,
    length_limit: float,
    support_zones: list[Rect] | None,
) -> CandidatePosition | None:
    """
    Finds a compact non-centered fallback position.
    """

    if layer.placements:
        if abs(layer.height - orientation.height) > HEIGHT_MATCH_TOLERANCE + EPS:
            return None

    best_position = None
    best_score = None

    for x, y in candidate_xy_points_compact(layer, orientation, support_zones):
        if x + orientation.length > length_limit + EPS:
            continue

        if y + orientation.width > width_limit + EPS:
            continue

        if has_collision(layer, x, y, orientation.length, orientation.width):
            continue

        support_valid, support_ratio, side_overhang = check_support(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        if not support_valid:
            continue

        new_layer_length = max(layer_used_length(layer), x + orientation.length)
        new_layer_width = max(layer_used_width(layer), y + orientation.width)

        center_offset = support_center_offset(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        waste = support_waste(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        balance_offset = layer_balance_offset(
            layer=layer,
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
        )

        position = CandidatePosition(
            x=x,
            y=y,
            new_layer_length=new_layer_length,
            new_layer_width=new_layer_width,
            support_ratio=support_ratio,
            side_overhang=side_overhang,
            center_offset=center_offset,
            support_waste=waste,
            layer_balance_offset=balance_offset,
        )

        # Main priority is fitting more on the same layer without overhang.
        score = (
            side_overhang * OVERHANG_SCORE_WEIGHT,
            -support_ratio,
            new_layer_length * new_layer_width,
            new_layer_width,
            new_layer_length,
            y,
            x,
            waste * SUPPORT_WASTE_WEIGHT,
            balance_offset * LAYER_BALANCE_WEIGHT,
            center_offset * CENTER_OFFSET_WEIGHT,
        )

        if best_score is None or score < best_score:
            best_score = score
            best_position = position

    return best_position


def build_compact_fallback_layer(
    remaining_pairs: list[tuple[int, Item]],
    support_zones: list[Rect] | None,
    z: float,
    width_limit: float,
    length_limit: float,
    strategy: str,
) -> tuple[Layer | None, list[int]]:
    """
    Builds one fallback layer and tries to fit multiple cartons on it.
    """

    layer = Layer(
        z=z,
        height=0.0,
        placements=[],
    )

    placed_indexes: list[int] = []

    while True:
        best_candidate = None
        best_score = None

        for item_index, item in remaining_pairs:
            if item_index in placed_indexes:
                continue

            for orientation in get_orientations(item.carton):
                if orientation.length > length_limit + EPS:
                    continue

                if orientation.width > width_limit + EPS:
                    continue

                if layer.placements:
                    if abs(layer.height - orientation.height) > HEIGHT_MATCH_TOLERANCE + EPS:
                        continue

                    test_layer = layer
                    height_after = layer.height
                else:
                    test_layer = Layer(
                        z=z,
                        height=orientation.height,
                        placements=[],
                    )
                    height_after = orientation.height

                position = find_compact_fallback_position(
                    layer=test_layer,
                    orientation=orientation,
                    width_limit=width_limit,
                    length_limit=length_limit,
                    support_zones=support_zones,
                )

                if position is None:
                    continue

                area = orientation.length * orientation.width
                long_side = max(orientation.length, orientation.width)
                short_side = min(orientation.length, orientation.width)

                if strategy == "large_first":
                    item_priority = (-area, -long_side, -short_side)
                elif strategy == "long_first":
                    item_priority = (-long_side, -area, -short_side)
                elif strategy == "height_first":
                    item_priority = (-orientation.height, -area, -long_side)
                else:
                    item_priority = (-area, -long_side, -short_side)

                # Prefer candidates that add another item to the current layer
                # with compact bottom-left placement.
                candidate_score = (
                    item_priority,
                    position.side_overhang * OVERHANG_SCORE_WEIGHT,
                    -position.support_ratio,
                    position.new_layer_length * position.new_layer_width,
                    position.new_layer_width,
                    position.new_layer_length,
                    height_after,
                    position.y,
                    position.x,
                )

                if best_score is None or candidate_score < best_score:
                    best_score = candidate_score
                    best_candidate = (
                        item_index,
                        item,
                        orientation,
                        position,
                    )

        if best_candidate is None:
            break

        item_index, item, orientation, position = best_candidate

        if not layer.placements:
            layer.height = orientation.height
        elif orientation.height > layer.height + EPS:
            layer.height = orientation.height

        place_item_in_layer(
            layer=layer,
            item=item,
            orientation=orientation,
            position=position,
            layer_number=0,
        )

        placed_indexes.append(item_index)

    if not layer.placements:
        return None, []

    return layer, placed_indexes


def compact_fallback_pack_for_limits(
    items: list[Item],
    length_limit: float,
    width_limit: float,
    strategy: str,
) -> SkidPlan | None:
    """
    Compact fallback pack for one fixed internal length/width limit.
    """

    indexed_items = list(enumerate(items))
    remaining_pairs = indexed_items[:]
    layers: list[Layer] = []
    z = 0.0

    while remaining_pairs:
        if z > MAX_LOADED_HEIGHT + EPS:
            return None

        support_zones = None if not layers else get_support_zones_from_layer(layers[-1])

        if layers and not support_zones:
            return None

        layer, placed_indexes = build_compact_fallback_layer(
            remaining_pairs=remaining_pairs,
            support_zones=support_zones,
            z=z,
            width_limit=width_limit,
            length_limit=length_limit,
            strategy=strategy,
        )

        if layer is None or not placed_indexes:
            return None

        layers.append(layer)
        z += layer.height

        remaining_pairs = [
            (item_index, item)
            for item_index, item in remaining_pairs
            if item_index not in placed_indexes
        ]

    layers = relabel_layers(layers)

    actual_height = current_actual_height(layers)

    if actual_height > MAX_LOADED_HEIGHT + EPS:
        return None

    used_length = current_internal_length(layers)
    used_width = current_internal_width(layers)

    skid_l = final_skid_length(used_length)
    skid_w = final_skid_width(used_width)

    if not is_within_skid_limits(skid_l, skid_w):
        return None

    plan = SkidPlan(
        skid_length=skid_l,
        skid_width=skid_w,
        skid_height=actual_height + HEIGHT_CLEARANCE,
        layers=layers,
    )

    # Only center the entire stack as one rigid unit.
    # Do NOT center individual upper layers in this fallback, because that is
    # what can split usable side space and increase stack height.
    centered = center_whole_plan_on_skid(plan)

    if plan_is_valid(centered):
        return centered

    if plan_is_valid(plan):
        return plan

    return None


def fallback_compact_noncentered_plan(items: list[Item]) -> SkidPlan | None:
    """
    Second-pass fallback.

    The normal optimizer tries compact dimensions and then centers the plan.
    If that fails, this fallback tries larger internal footprints and packs
    more cartons per layer using bottom-left placement, then only centers the
    whole finished stack.
    """

    if not items:
        return None

    best_plan = None
    best_score = None

    length_limits = generate_fallback_length_limits(items)
    width_limits = generate_candidate_widths(items)
    strategies = ["large_first", "long_first", "height_first"]

    for length_limit in length_limits:
        for width_limit in width_limits:
            if width_limit > max_internal_width() + EPS:
                continue

            if length_limit > max_internal_length() + EPS:
                continue

            for strategy in strategies:
                plan = compact_fallback_pack_for_limits(
                    items=items,
                    length_limit=length_limit,
                    width_limit=width_limit,
                    strategy=strategy,
                )

                if plan is None:
                    continue

                actual_height = current_actual_height(plan.layers)
                total_overhang, support_loss, waste = plan_support_score(plan)

                # In fallback mode, height matters more because the point is to
                # avoid unnecessary one-by-one stacking. Area still matters, but
                # it is allowed to grow if it creates fewer/lower layers.
                score = (
                    actual_height,
                    plan.area,
                    total_overhang * OVERHANG_SCORE_WEIGHT,
                    support_loss * OVERHANG_SCORE_WEIGHT,
                    waste * SUPPORT_WASTE_WEIGHT,
                    plan.skid_length,
                    plan.skid_width,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_plan = plan

    return best_plan


# ============================================================
# LAYERED FALLBACK PACKING
# ============================================================

def find_fallback_position_in_layer(
    layer: Layer,
    orientation: Orientation,
    width_limit: float,
    length_limit: float | None,
    support_zones: list[Rect] | None,
) -> CandidatePosition | None:
    """
    Position finder used only by the fallback packer.

    Difference from the main finder:
    - It prefers bottom-left / compact placement over centered placement.
    - That helps leave usable side strips for other cartons in the same layer.
    """

    if layer.placements:
        if abs(layer.height - orientation.height) > HEIGHT_MATCH_TOLERANCE + EPS:
            return None

    best_position = None
    best_score = None

    for x, y in candidate_xy_points(layer, orientation, support_zones):
        if y + orientation.width > width_limit + EPS:
            continue

        if length_limit is not None and x + orientation.length > length_limit + EPS:
            continue

        if has_collision(layer, x, y, orientation.length, orientation.width):
            continue

        support_valid, support_ratio, side_overhang = check_support(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        if not support_valid:
            continue

        new_layer_length = max(layer_used_length(layer), x + orientation.length)
        new_layer_width = max(layer_used_width(layer), y + orientation.width)

        center_offset = support_center_offset(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        waste = support_waste(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
            support_zones=support_zones,
        )

        balance_offset = layer_balance_offset(
            layer=layer,
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
        )

        position = CandidatePosition(
            x=x,
            y=y,
            new_layer_length=new_layer_length,
            new_layer_width=new_layer_width,
            support_ratio=support_ratio,
            side_overhang=side_overhang,
            center_offset=center_offset,
            support_waste=waste,
            layer_balance_offset=balance_offset,
        )

        # Lower is better.
        #
        # This fallback intentionally gives compact layer usage priority over
        # centering. That lets a large carton sit at y=0 and a skinny carton fit
        # beside it, instead of centering the large carton and splitting the
        # leftover width into unusable strips.
        score = (
            new_layer_length * new_layer_width,
            new_layer_width,
            new_layer_length,
            side_overhang * OVERHANG_SCORE_WEIGHT,
            -support_ratio,
            y,
            x,
            center_offset * CENTER_OFFSET_WEIGHT,
            waste * SUPPORT_WASTE_WEIGHT,
            balance_offset * LAYER_BALANCE_WEIGHT,
        )

        if best_score is None or score < best_score:
            best_score = score
            best_position = position

    return best_position


def build_fallback_layer(
    remaining_pairs: list[tuple[int, Item]],
    support_zones: list[Rect],
    z: float,
    width_limit: float,
    length_limit: float | None,
) -> tuple[Layer | None, list[int]]:
    """
    Builds one layer above the previous layer.

    It tries to pack as many remaining cartons as possible onto the current
    support zones. This is generic and not hardcoded to any row number.
    """

    layer = Layer(
        z=z,
        height=0.0,
        placements=[],
    )

    placed_item_indexes: list[int] = []

    while True:
        best_candidate = None
        best_score = None

        for item_index, item in remaining_pairs:
            if item_index in placed_item_indexes:
                continue

            for orientation in get_orientations(item.carton):
                if orientation.width > width_limit + EPS:
                    continue

                if length_limit is not None and orientation.length > length_limit + EPS:
                    continue

                if orientation.height > MAX_LOADED_HEIGHT + EPS:
                    continue

                if layer.placements:
                    if abs(layer.height - orientation.height) > HEIGHT_MATCH_TOLERANCE + EPS:
                        continue

                    test_layer = layer
                else:
                    test_layer = Layer(
                        z=z,
                        height=orientation.height,
                        placements=[],
                    )

                position = find_fallback_position_in_layer(
                    layer=test_layer,
                    orientation=orientation,
                    width_limit=width_limit,
                    length_limit=length_limit,
                    support_zones=support_zones,
                )

                if position is None:
                    continue

                item_area = orientation.length * orientation.width

                # Prefer placing larger cartons first, while still honoring
                # compact position quality.
                candidate_score = (
                    -item_area,
                    position.new_layer_length * position.new_layer_width,
                    position.new_layer_width,
                    position.new_layer_length,
                    position.side_overhang * OVERHANG_SCORE_WEIGHT,
                    -position.support_ratio,
                    position.y,
                    position.x,
                )

                if best_score is None or candidate_score < best_score:
                    best_score = candidate_score
                    best_candidate = (
                        item_index,
                        item,
                        orientation,
                        position,
                    )

        if best_candidate is None:
            break

        item_index, item, orientation, position = best_candidate

        if not layer.placements:
            layer.height = orientation.height
        elif orientation.height > layer.height + EPS:
            layer.height = orientation.height

        place_item_in_layer(
            layer=layer,
            item=item,
            orientation=orientation,
            position=position,
            layer_number=0,  # corrected after all layers are built
        )

        placed_item_indexes.append(item_index)

    if not layer.placements:
        return None, []

    return layer, placed_item_indexes


def relabel_layers(layers: list[Layer]) -> list[Layer]:
    """
    Rebuilds layers so every placement has the correct layer_number.
    """

    relabeled_layers = []

    for layer_number, layer in enumerate(layers, start=1):
        placements = []

        for p in layer.placements:
            placements.append(
                Placement(
                    csv_row_number=p.csv_row_number,
                    copy_number=p.copy_number,
                    x=p.x,
                    y=p.y,
                    z=p.z,
                    length=p.length,
                    width=p.width,
                    height=p.height,
                    orientation=p.orientation,
                    layer_number=layer_number,
                )
            )

        relabeled_layers.append(
            Layer(
                z=layer.z,
                height=layer.height,
                placements=placements,
            )
        )

    return relabeled_layers


def fallback_layered_pack_plan(items: list[Item]) -> SkidPlan | None:
    """
    Stronger generic fallback.

    Instead of stacking one carton per layer, it builds each upper layer by
    packing as many cartons as possible on the support layer below. This catches
    cases where a skinny carton should sit beside a wider carton on the same
    layer.
    """

    if not items:
        return None

    base_result = choose_fallback_base_and_orientations(items)

    if base_result is None:
        return None

    base_length, base_width, chosen_orientations = base_result

    indexed_items = list(enumerate(items))

    # Choose the largest footprint carton as the base.
    base_item_index, base_item = max(
        indexed_items,
        key=lambda pair: (
            chosen_orientations[pair[0]].length * chosen_orientations[pair[0]].width,
            max(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
        ),
    )

    base_orientation = chosen_orientations[base_item_index]

    base_layer = Layer(
        z=0.0,
        height=base_orientation.height,
        placements=[],
    )

    place_item_in_layer(
        layer=base_layer,
        item=base_item,
        orientation=base_orientation,
        position=CandidatePosition(
            x=0.0,
            y=0.0,
            new_layer_length=base_orientation.length,
            new_layer_width=base_orientation.width,
            support_ratio=1.0,
            side_overhang=0.0,
            center_offset=0.0,
            support_waste=0.0,
            layer_balance_offset=0.0,
        ),
        layer_number=1,
    )

    layers = [base_layer]

    remaining_pairs = [
        (item_index, item)
        for item_index, item in indexed_items
        if item_index != base_item_index
    ]

    width_limit = max_internal_width()
    length_limit = max_internal_length()

    z = base_layer.height

    while remaining_pairs:
        support_zones = get_support_zones_from_layer(layers[-1])

        if not support_zones:
            return None

        next_layer, placed_indexes = build_fallback_layer(
            remaining_pairs=remaining_pairs,
            support_zones=support_zones,
            z=z,
            width_limit=width_limit,
            length_limit=length_limit,
        )

        if next_layer is None or not placed_indexes:
            return None

        layers.append(next_layer)
        z += next_layer.height

        remaining_pairs = [
            (item_index, item)
            for item_index, item in remaining_pairs
            if item_index not in placed_indexes
        ]

        if z > MAX_LOADED_HEIGHT + EPS:
            return None

    layers = relabel_layers(layers)

    actual_height = current_actual_height(layers)

    if actual_height > MAX_LOADED_HEIGHT + EPS:
        return None

    plan = SkidPlan(
        skid_length=final_skid_length(base_length),
        skid_width=final_skid_width(base_width),
        skid_height=actual_height + HEIGHT_CLEARANCE,
        layers=layers,
    )

    centered = center_plan_on_skid(plan)

    if plan_is_valid(centered):
        return centered

    return None


def create_failure_visualization_plan(items: list[Item]) -> SkidPlan | None:
    """
    Creates an intentionally visual debug plan for a failed group.

    This is used only when all real packing methods fail. The returned plan may
    show unsafe overhang, but that is useful because the Plotly hover labels show
    which carton/row is causing the issue.
    """

    base_result = choose_fallback_base_and_orientations(items)

    if base_result is None:
        return None

    base_length, base_width, chosen_orientations = base_result

    indexed_items = list(enumerate(items))
    indexed_items.sort(
        key=lambda pair: (
            chosen_orientations[pair[0]].length * chosen_orientations[pair[0]].width,
            min(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
            max(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
        ),
        reverse=True,
    )

    layers: list[Layer] = []
    z = 0.0

    for layer_number, (item_index, item) in enumerate(indexed_items, start=1):
        orientation = chosen_orientations[item_index]

        x = (base_length - orientation.length) / 2
        y = (base_width - orientation.width) / 2

        layer = make_layer(
            z=z,
            height=orientation.height,
        )

        place_item_in_layer(
            layer=layer,
            item=item,
            orientation=orientation,
            position=CandidatePosition(
                x=x,
                y=y,
                new_layer_length=base_length,
                new_layer_width=base_width,
                support_ratio=1.0,
                side_overhang=0.0,
                center_offset=0.0,
                support_waste=0.0,
                layer_balance_offset=0.0,
            ),
            layer_number=layer_number,
        )

        layers.append(layer)
        z += orientation.height

    return SkidPlan(
        skid_length=final_skid_length(base_length),
        skid_width=final_skid_width(base_width),
        skid_height=current_actual_height(layers) + HEIGHT_CLEARANCE,
        layers=layers,
    )


# ============================================================
# NO VALID SKID DIAGNOSTICS
# ============================================================

def orientation_status(
    orientation: Orientation,
    length_limit: float | None,
) -> tuple[bool, list[str]]:
    """
    Checks one orientation against the hard physical limits.
    """

    reasons = []

    if orientation.width > max_internal_width() + EPS:
        reasons.append(
            f"width {round(orientation.width, 2)} > max internal width {round(max_internal_width(), 2)}"
        )

    if length_limit is not None and orientation.length > length_limit + EPS:
        reasons.append(
            f"length {round(orientation.length, 2)} > max internal length {round(length_limit, 2)}"
        )

    if orientation.height > MAX_LOADED_HEIGHT + EPS:
        reasons.append(
            f"height {round(orientation.height, 2)} > max loaded height {round(MAX_LOADED_HEIGHT, 2)}"
        )

    return len(reasons) == 0, reasons


def print_fallback_stack_diagnostics(items: list[Item]) -> None:
    """
    Explains why the conservative fallback stack failed.

    The fallback stacks one carton per layer, centered. That only works if every
    upper carton is supported by the carton directly below it.
    """

    base_result = choose_fallback_base_and_orientations(items)

    if base_result is None:
        print("  Fallback stack: could not find a base footprint that every carton can fit inside.")
        return

    base_length, base_width, chosen_orientations = base_result

    indexed_items = list(enumerate(items))
    indexed_items.sort(
        key=lambda pair: (
            chosen_orientations[pair[0]].length * chosen_orientations[pair[0]].width,
            min(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
            max(chosen_orientations[pair[0]].length, chosen_orientations[pair[0]].width),
        ),
        reverse=True,
    )

    print(
        "  Fallback stack base footprint:",
        f"{round(base_length, 2)} x {round(base_width, 2)}",
        f"(skid {round(final_skid_length(base_length), 2)} x {round(final_skid_width(base_width), 2)})",
    )

    previous_rect = None
    previous_item = None
    z = 0.0

    for layer_number, (item_index, item) in enumerate(indexed_items, start=1):
        orientation = chosen_orientations[item_index]

        x = (base_length - orientation.length) / 2
        y = (base_width - orientation.width) / 2

        current_rect = Rect(
            x=x,
            y=y,
            length=orientation.length,
            width=orientation.width,
        )

        print(
            f"  Fallback layer {layer_number}:",
            f"Excel/CSV row {item.csv_row_number},",
            f"size {round(orientation.length, 2)} x {round(orientation.width, 2)} x {round(orientation.height, 2)},",
            f"x={round(x, 2)}, y={round(y, 2)}, z={round(z, 2)}",
        )

        if previous_rect is not None:
            valid, support_ratio, side_overhang = check_support(
                x=current_rect.x,
                y=current_rect.y,
                length=current_rect.length,
                width=current_rect.width,
                support_zones=[previous_rect],
            )

            if not valid:
                print("  Fallback stack failed here:")
                print(
                    f"    Layer {layer_number} row {item.csv_row_number} is not supported enough by "
                    f"layer {layer_number - 1} row {previous_item.csv_row_number}."
                )
                print(
                    f"    support_ratio={round(support_ratio, 4)}, "
                    f"required={MIN_SUPPORT_RATIO}, "
                    f"side_overhang={round(side_overhang, 4)}, "
                    f"allowed={MAX_SIDE_OVERHANG}"
                )
                return

        previous_rect = current_rect
        previous_item = item
        z += orientation.height

    total_height = sum(item.carton.height for _, item in indexed_items)

    if total_height > MAX_LOADED_HEIGHT + EPS:
        print(
            f"  Fallback stack failed: total stacked height {round(total_height, 2)} "
            f"> max loaded height {round(MAX_LOADED_HEIGHT, 2)}"
        )
        return

    print("  Fallback stack looked geometrically valid, but plan validation still failed.")


def print_no_valid_skid_diagnostics(
    items: list[Item],
    debug_label: str | None = None,
) -> None:
    """
    Prints useful console information when no valid skid can be created.
    """

    title = "[NO VALID SKID DEBUG]"

    if debug_label:
        title += f" Group {debug_label}"

    print()
    print("=" * len(title))
    print(title)
    print("=" * len(title))

    print(f"Cartons in group: {len(items)}")
    print(
        "Limits:",
        f"max_internal_length={round(max_internal_length(), 2) if max_internal_length() is not None else 'None'},",
        f"max_internal_width={round(max_internal_width(), 2)},",
        f"max_loaded_height={round(MAX_LOADED_HEIGHT, 2)}",
    )
    print(
        "Support settings:",
        f"MIN_SUPPORT_RATIO={MIN_SUPPORT_RATIO},",
        f"MAX_SIDE_OVERHANG={MAX_SIDE_OVERHANG},",
        f"HEIGHT_MATCH_TOLERANCE={HEIGHT_MATCH_TOLERANCE},",
        f"SUPPORT_HEIGHT_TOLERANCE={SUPPORT_HEIGHT_TOLERANCE}",
    )

    raw_total_height = sum(item.carton.height for item in items)
    print(
        f"Raw sum of carton heights if stacked one-by-one: {round(raw_total_height, 2)}",
        f"(output with clearance would be {round(raw_total_height + HEIGHT_CLEARANCE, 2)})",
    )

    length_limit = max_internal_length()

    print()
    print("Carton orientation checks:")

    any_item_has_no_valid_orientation = False

    for item in items:
        orientations = list(get_orientations(item.carton))
        valid_count = 0

        print(
            f"  Row {item.csv_row_number}, copy {item.copy_number}:",
            f"original {round(item.carton.length, 2)} x {round(item.carton.width, 2)} x {round(item.carton.height, 2)}",
        )

        for orientation in orientations:
            ok, reasons = orientation_status(
                orientation=orientation,
                length_limit=length_limit,
            )

            if ok:
                valid_count += 1
                print(
                    "    OK:",
                    f"{round(orientation.length, 2)} x {round(orientation.width, 2)} x {round(orientation.height, 2)}",
                    f"({orientation.label})",
                )
            else:
                print(
                    "    REJECT:",
                    f"{round(orientation.length, 2)} x {round(orientation.width, 2)} x {round(orientation.height, 2)}",
                    "because",
                    "; ".join(reasons),
                )

        if valid_count == 0:
            any_item_has_no_valid_orientation = True
            print("    Problem: this carton has no valid flat orientation.")

    print()
    width_candidates = generate_candidate_widths(items)

    print(f"Width candidates tried: {len(width_candidates)}")

    if width_candidates:
        print(
            "  smallest candidates:",
            [round(value, 2) for value in width_candidates[:10]],
        )
        print(
            "  largest candidates:",
            [round(value, 2) for value in width_candidates[-10:]],
        )
    else:
        print("  Problem: no valid width candidates were generated.")

    if any_item_has_no_valid_orientation:
        print()
        print("Likely cause: at least one carton cannot fit within hard length/width/height limits in any flat orientation.")
    else:
        print()
        print("All cartons have at least one valid flat orientation.")
        print("Likely cause: support/stacking rules prevented a safe layered arrangement.")
        print("Note: v4 allows similar-height cartons to grow middle layers; older files did not.")

    print()
    print_fallback_stack_diagnostics(items)

    print("=" * len(title))
    print()


# ============================================================
# FINAL PLAN SCORING
# ============================================================

def plan_support_score(plan: SkidPlan) -> tuple[float, float, float]:
    total_overhang = 0.0
    total_support_loss = 0.0
    total_waste = 0.0

    for layer_index in range(1, len(plan.layers)):
        support_zones = get_support_zones_from_layer(plan.layers[layer_index - 1])

        for p in plan.layers[layer_index].placements:
            valid, support_ratio, side_overhang = check_support(
                x=p.x,
                y=p.y,
                length=p.length,
                width=p.width,
                support_zones=support_zones,
            )

            if not valid:
                total_overhang += 1_000_000.0
                total_support_loss += 1_000_000.0
                continue

            total_overhang += side_overhang
            total_support_loss += max(0.0, 1.0 - support_ratio)

            total_waste += support_waste(
                x=p.x,
                y=p.y,
                length=p.length,
                width=p.width,
                support_zones=support_zones,
            )

    return total_overhang, total_support_loss, total_waste



# ============================================================
# PRACTICAL BEAM-SEARCH OPTIMIZER
# ============================================================

@dataclass(frozen=True)
class BeamSearchSettings:
    """
    Controls how wide/deep the practical optimizer searches.

    Increasing beam_width and max_positions_per_target improves search quality,
    but it also increases runtime.
    """

    name: str
    beam_width: int
    max_positions_per_target: int
    area_weight: float
    height_weight: float
    center_weight: float
    z_center_weight: float
    layer_weight: float
    support_weight: float
    footprint_growth_weight: float


@dataclass(frozen=True)
class PlanState:
    """
    One partial packing state during beam search.
    """

    layers: list[Layer]


PRACTICAL_SEARCH_SETTINGS = [
    BeamSearchSettings(
        name="balanced practical beam search",
        beam_width=220,
        max_positions_per_target=18,
        area_weight=1.00,
        height_weight=135.0,
        center_weight=95.0,
        z_center_weight=45.0,
        layer_weight=120.0,
        support_weight=3000.0,
        footprint_growth_weight=0.50,
    ),
    BeamSearchSettings(
        name="small skid practical beam search",
        beam_width=260,
        max_positions_per_target=20,
        area_weight=1.35,
        height_weight=95.0,
        center_weight=80.0,
        z_center_weight=35.0,
        layer_weight=95.0,
        support_weight=3000.0,
        footprint_growth_weight=0.75,
    ),
    BeamSearchSettings(
        name="low-height practical beam search",
        beam_width=260,
        max_positions_per_target=20,
        area_weight=0.85,
        height_weight=210.0,
        center_weight=110.0,
        z_center_weight=55.0,
        layer_weight=165.0,
        support_weight=3000.0,
        footprint_growth_weight=0.35,
    ),
]


def item_sort_key_big_flat(item: Item) -> tuple:
    """
    Sorts cartons so the biggest/flattest cartons are placed first.

    This tends to create a practical base: large, stable cartons low in the stack.
    """

    footprint = item.carton.length * item.carton.width
    biggest_side = max(item.carton.length, item.carton.width)
    flatness = footprint / max(item.carton.height, EPS)

    return (
        footprint,
        flatness,
        biggest_side,
        -item.carton.height,
    )


def item_sort_key_long(item: Item) -> tuple:
    """
    Alternate order that handles long skinny cartons earlier.
    """

    footprint = item.carton.length * item.carton.width
    biggest_side = max(item.carton.length, item.carton.width)
    smallest_side = min(item.carton.length, item.carton.width)

    return (
        biggest_side,
        footprint,
        -smallest_side,
        -item.carton.height,
    )


def item_sort_key_height_group(item: Item) -> tuple:
    """
    Alternate order that groups similar taller cartons earlier.
    """

    footprint = item.carton.length * item.carton.width

    return (
        item.carton.height,
        footprint,
        max(item.carton.length, item.carton.width),
    )


def plan_layers_copy(layers: list[Layer]) -> list[Layer]:
    """
    Copies layers and placements.

    Placement objects are treated as immutable here, so it is safe to reuse them
    unless their z coordinate changes.
    """

    copied_layers = []

    for layer in layers:
        copied_layers.append(
            Layer(
                z=layer.z,
                height=layer.height,
                placements=list(layer.placements),
            )
        )

    return copied_layers


def relabel_and_rez_layers(layers: list[Layer]) -> list[Layer]:
    """
    Rebuilds layers so z coordinates and layer numbers are consistent.
    """

    rebuilt_layers = []
    z = 0.0

    for layer_number, layer in enumerate(layers, start=1):
        placements = []

        for p in layer.placements:
            placements.append(
                Placement(
                    csv_row_number=p.csv_row_number,
                    copy_number=p.copy_number,
                    x=p.x,
                    y=p.y,
                    z=z,
                    length=p.length,
                    width=p.width,
                    height=p.height,
                    orientation=p.orientation,
                    layer_number=layer_number,
                )
            )

        rebuilt_layers.append(
            Layer(
                z=z,
                height=layer.height,
                placements=placements,
            )
        )

        z += layer.height

    return rebuilt_layers


def build_plan_from_layers(layers: list[Layer]) -> SkidPlan | None:
    """
    Creates a SkidPlan from layers using the actual used footprint.
    """

    if not layers:
        return None

    layers = relabel_and_rez_layers(layers)

    internal_length = current_internal_length(layers)
    internal_width = current_internal_width(layers)
    actual_height = current_actual_height(layers)

    if actual_height > MAX_LOADED_HEIGHT + EPS:
        return None

    skid_l = final_skid_length(internal_length)
    skid_w = final_skid_width(internal_width)
    skid_h = actual_height + HEIGHT_CLEARANCE

    if not is_within_skid_limits(skid_l, skid_w):
        return None

    plan = SkidPlan(
        skid_length=skid_l,
        skid_width=skid_w,
        skid_height=skid_h,
        layers=layers,
    )

    if not plan_is_valid(plan):
        return None

    return plan


def state_signature(layers: list[Layer]) -> tuple:
    """
    Dedupes equivalent beam-search states.
    """

    signature = []

    for layer_index, layer in enumerate(layers):
        for p in layer.placements:
            signature.append(
                (
                    p.csv_row_number,
                    p.copy_number,
                    layer_index,
                    round(p.x, 2),
                    round(p.y, 2),
                    round(p.length, 2),
                    round(p.width, 2),
                    round(p.height, 2),
                )
            )

    return tuple(sorted(signature))


def candidate_xy_points_practical(
    layer: Layer,
    orientation: Orientation,
    support_zones: list[Rect] | None,
    hard_length_limit: float,
    hard_width_limit: float,
) -> list[tuple[float, float]]:
    """
    Generates practical candidate positions.

    It deliberately includes:
    - bottom-left / edge-aligned positions
    - centered-on-support positions
    - side-by-side positions
    - right/back aligned positions

    This gives the optimizer a chance to compare centering, stacking, and
    side-by-side placement instead of forcing only one style.
    """

    xs = {0.0}
    ys = {0.0}

    # Current layer edges: enables side-by-side packing.
    for p in layer.placements:
        xs.update(
            {
                p.x,
                p.x + p.length,
                p.x + p.length - orientation.length,
                p.x + (p.length - orientation.length) / 2,
            }
        )
        ys.update(
            {
                p.y,
                p.y + p.width,
                p.y + p.width - orientation.width,
                p.y + (p.width - orientation.width) / 2,
            }
        )

    # Hard-boundary alignment.
    xs.update(
        {
            hard_length_limit - orientation.length,
            (hard_length_limit - orientation.length) / 2,
        }
    )
    ys.update(
        {
            hard_width_limit - orientation.width,
            (hard_width_limit - orientation.width) / 2,
        }
    )

    if support_zones is not None:
        support_min_x = min(zone.x for zone in support_zones)
        support_max_x = max(zone.x2 for zone in support_zones)
        support_min_y = min(zone.y for zone in support_zones)
        support_max_y = max(zone.y2 for zone in support_zones)

        # Support bounding box alignment.
        xs.update(
            {
                support_min_x,
                support_max_x - orientation.length,
                support_min_x + ((support_max_x - support_min_x) - orientation.length) / 2,
            }
        )
        ys.update(
            {
                support_min_y,
                support_max_y - orientation.width,
                support_min_y + ((support_max_y - support_min_y) - orientation.width) / 2,
            }
        )

        for zone in support_zones:
            xs.update(
                {
                    zone.x,
                    zone.x2 - orientation.length,
                    zone.x + (zone.length - orientation.length) / 2,
                    zone.x + MAX_SIDE_OVERHANG,
                    zone.x2 - orientation.length - MAX_SIDE_OVERHANG,
                    zone.x2 - orientation.length + MAX_SIDE_OVERHANG,
                }
            )
            ys.update(
                {
                    zone.y,
                    zone.y2 - orientation.width,
                    zone.y + (zone.width - orientation.width) / 2,
                    zone.y + MAX_SIDE_OVERHANG,
                    zone.y2 - orientation.width - MAX_SIDE_OVERHANG,
                    zone.y2 - orientation.width + MAX_SIDE_OVERHANG,
                }
            )

    points = []

    for x in xs:
        for y in ys:
            if x < -EPS or y < -EPS:
                continue

            x = round(max(0.0, x), 4)
            y = round(max(0.0, y), 4)

            if x + orientation.length > hard_length_limit + EPS:
                continue

            if y + orientation.width > hard_width_limit + EPS:
                continue

            points.append((x, y))

    # Deterministic order.
    return sorted(set(points), key=lambda point: (point[1], point[0]))


def placement_position_score(
    layer: Layer,
    orientation: Orientation,
    x: float,
    y: float,
    support_zones: list[Rect] | None,
    settings: BeamSearchSettings,
) -> tuple:
    """
    Scores one possible position before creating the full next state.
    """

    support_valid, support_ratio, side_overhang = check_support(
        x=x,
        y=y,
        length=orientation.length,
        width=orientation.width,
        support_zones=support_zones,
    )

    if not support_valid:
        return (float("inf"),)

    new_layer_length = max(layer_used_length(layer), x + orientation.length)
    new_layer_width = max(layer_used_width(layer), y + orientation.width)

    center_offset = support_center_offset(
        x=x,
        y=y,
        length=orientation.length,
        width=orientation.width,
        support_zones=support_zones,
    )

    waste = support_waste(
        x=x,
        y=y,
        length=orientation.length,
        width=orientation.width,
        support_zones=support_zones,
    )

    balance_offset = layer_balance_offset(
        layer=layer,
        x=x,
        y=y,
        length=orientation.length,
        width=orientation.width,
    )

    return (
        side_overhang * settings.support_weight,
        max(0.0, 1.0 - support_ratio) * settings.support_weight,
        new_layer_length * new_layer_width * settings.footprint_growth_weight,
        new_layer_width,
        new_layer_length,
        balance_offset * LAYER_BALANCE_WEIGHT,
        center_offset * CENTER_OFFSET_WEIGHT,
        waste * SUPPORT_WASTE_WEIGHT,
        y,
        x,
    )


def generate_top_positions_for_target(
    layer: Layer,
    orientation: Orientation,
    support_zones: list[Rect] | None,
    hard_length_limit: float,
    hard_width_limit: float,
    settings: BeamSearchSettings,
) -> list[tuple[float, float]]:
    """
    Returns the top practical positions for this carton/layer/orientation.
    """

    raw_points = candidate_xy_points_practical(
        layer=layer,
        orientation=orientation,
        support_zones=support_zones,
        hard_length_limit=hard_length_limit,
        hard_width_limit=hard_width_limit,
    )

    scored_points = []

    for x, y in raw_points:
        if has_collision(layer, x, y, orientation.length, orientation.width):
            continue

        score = placement_position_score(
            layer=layer,
            orientation=orientation,
            x=x,
            y=y,
            support_zones=support_zones,
            settings=settings,
        )

        if score[0] == float("inf"):
            continue

        scored_points.append((score, x, y))

    scored_points.sort(key=lambda value: value[0])

    return [
        (x, y)
        for _, x, y in scored_points[: settings.max_positions_per_target]
    ]


def add_item_to_existing_layer(
    layers: list[Layer],
    item: Item,
    orientation: Orientation,
    layer_index: int,
    x: float,
    y: float,
) -> list[Layer]:
    """
    Returns new layers after placing an item into an existing layer.
    """

    new_layers = plan_layers_copy(layers)
    target_layer = new_layers[layer_index]

    old_height = target_layer.height
    new_height = max(old_height, orientation.height)
    height_delta = new_height - old_height

    placement = Placement(
        csv_row_number=item.csv_row_number,
        copy_number=item.copy_number,
        x=x,
        y=y,
        z=target_layer.z,
        length=orientation.length,
        width=orientation.width,
        height=orientation.height,
        orientation=orientation.label,
        layer_number=layer_index + 1,
    )

    target_layer.placements.append(placement)
    target_layer.height = new_height

    if height_delta > EPS:
        # Shift layers above upward.
        for upper_index in range(layer_index + 1, len(new_layers)):
            upper_layer = new_layers[upper_index]
            shifted_placements = []

            for p in upper_layer.placements:
                shifted_placements.append(
                    Placement(
                        csv_row_number=p.csv_row_number,
                        copy_number=p.copy_number,
                        x=p.x,
                        y=p.y,
                        z=p.z + height_delta,
                        length=p.length,
                        width=p.width,
                        height=p.height,
                        orientation=p.orientation,
                        layer_number=p.layer_number,
                    )
                )

            new_layers[upper_index] = Layer(
                z=upper_layer.z + height_delta,
                height=upper_layer.height,
                placements=shifted_placements,
            )

    return relabel_and_rez_layers(new_layers)


def add_item_to_new_top_layer(
    layers: list[Layer],
    item: Item,
    orientation: Orientation,
    x: float,
    y: float,
) -> list[Layer]:
    """
    Returns new layers after placing an item into a new top layer.
    """

    new_layers = plan_layers_copy(layers)
    z = current_actual_height(new_layers)

    placement = Placement(
        csv_row_number=item.csv_row_number,
        copy_number=item.copy_number,
        x=x,
        y=y,
        z=z,
        length=orientation.length,
        width=orientation.width,
        height=orientation.height,
        orientation=orientation.label,
        layer_number=len(new_layers) + 1,
    )

    new_layers.append(
        Layer(
            z=z,
            height=orientation.height,
            placements=[placement],
        )
    )

    return relabel_and_rez_layers(new_layers)


def center_whole_plan_only(plan: SkidPlan) -> SkidPlan:
    """
    Centers the finished stack as one rigid object.

    This does not center individual layers, because that can create impractical
    layouts by splitting usable space on support layers.
    """

    centered = center_whole_plan_on_skid(plan)

    if plan_is_valid(centered):
        return centered

    return plan


def plan_center_of_mass(plan: SkidPlan) -> tuple[float, float, float]:
    """
    Approximate center of mass using carton volume as weight.

    We do not have actual weights, so volume is the best available proxy.
    """

    total_weight = 0.0
    weighted_x = 0.0
    weighted_y = 0.0
    weighted_z = 0.0

    for layer in plan.layers:
        for p in layer.placements:
            weight = p.length * p.width * p.height

            total_weight += weight
            weighted_x += weight * (p.x + p.length / 2)
            weighted_y += weight * (p.y + p.width / 2)
            weighted_z += weight * (p.z + p.height / 2)

    if total_weight <= EPS:
        return 0.0, 0.0, 0.0

    return (
        weighted_x / total_weight,
        weighted_y / total_weight,
        weighted_z / total_weight,
    )


def plan_stability_score(plan: SkidPlan) -> tuple[float, float, float]:
    """
    Returns support/overhang stability metrics.

    Lower is better.
    """

    total_side_overhang = 0.0
    total_support_loss = 0.0
    worst_side_overhang = 0.0

    for layer_index in range(1, len(plan.layers)):
        support_zones = get_support_zones_from_layer(plan.layers[layer_index - 1])

        for p in plan.layers[layer_index].placements:
            valid, support_ratio, side_overhang = check_support(
                x=p.x,
                y=p.y,
                length=p.length,
                width=p.width,
                support_zones=support_zones,
            )

            if not valid:
                total_side_overhang += 1_000_000.0
                total_support_loss += 1_000_000.0
                worst_side_overhang = 1_000_000.0
                continue

            total_side_overhang += side_overhang
            total_support_loss += max(0.0, 1.0 - support_ratio)
            worst_side_overhang = max(worst_side_overhang, side_overhang)

    return total_side_overhang, total_support_loss, worst_side_overhang


def practical_plan_score(plan: SkidPlan, settings: BeamSearchSettings) -> tuple:
    """
    Scores a completed or partial plan.

    Lower is better.

    The order is intentionally practical:
    - small skid footprint
    - lower loaded height
    - stable support/no overhang
    - center of mass near skid center
    - fewer layers
    """

    actual_height = current_actual_height(plan.layers)
    area = plan.skid_length * plan.skid_width
    com_x, com_y, com_z = plan_center_of_mass(plan)

    skid_center_x = plan.skid_length / 2
    skid_center_y = plan.skid_width / 2

    horizontal_com_offset = math.sqrt(
        (com_x - skid_center_x) ** 2
        + (com_y - skid_center_y) ** 2
    )

    total_overhang, support_loss, worst_overhang = plan_stability_score(plan)

    layer_count = len(plan.layers)

    score_value = (
        area * settings.area_weight
        + actual_height * settings.height_weight
        + horizontal_com_offset * settings.center_weight
        + com_z * settings.z_center_weight
        + layer_count * settings.layer_weight
        + total_overhang * settings.support_weight
        + support_loss * settings.support_weight
    )

    return (
        score_value,
        area,
        actual_height,
        worst_overhang,
        horizontal_com_offset,
        com_z,
        layer_count,
        plan.skid_length,
        plan.skid_width,
    )


def generate_next_states(
    state: PlanState,
    item: Item,
    settings: BeamSearchSettings,
) -> list[PlanState]:
    """
    Generates valid next states by trying:
    - same-layer placement
    - new-layer placement
    - both flat orientations
    - centered, edge-aligned, and side-by-side positions
    """

    next_states: list[PlanState] = []

    hard_length_limit = max_internal_length()

    if hard_length_limit is None:
        hard_length_limit = max(
            orientation.length
            for orientation in get_orientations(item.carton)
        )

    hard_width_limit = max_internal_width()

    for orientation in get_orientations(item.carton):
        if orientation.length > hard_length_limit + EPS:
            continue

        if orientation.width > hard_width_limit + EPS:
            continue

        if orientation.height > MAX_LOADED_HEIGHT + EPS:
            continue

        # Empty state: create the base layer at origin.
        if not state.layers:
            new_layers = add_item_to_new_top_layer(
                layers=[],
                item=item,
                orientation=orientation,
                x=0.0,
                y=0.0,
            )

            plan = build_plan_from_layers(new_layers)

            if plan is not None:
                next_states.append(PlanState(layers=new_layers))

            continue

        # Try every existing layer.
        for layer_index, layer in enumerate(state.layers):
            if abs(layer.height - orientation.height) > HEIGHT_MATCH_TOLERANCE + EPS:
                continue

            if layer_index == 0:
                support_zones = None
            else:
                support_zones = get_support_zones_from_layer(state.layers[layer_index - 1])

                if not support_zones:
                    continue

            positions = generate_top_positions_for_target(
                layer=layer,
                orientation=orientation,
                support_zones=support_zones,
                hard_length_limit=hard_length_limit,
                hard_width_limit=hard_width_limit,
                settings=settings,
            )

            for x, y in positions:
                new_layers = add_item_to_existing_layer(
                    layers=state.layers,
                    item=item,
                    orientation=orientation,
                    layer_index=layer_index,
                    x=x,
                    y=y,
                )

                plan = build_plan_from_layers(new_layers)

                if plan is not None:
                    next_states.append(PlanState(layers=new_layers))

        # Try a new top layer.
        support_zones = get_support_zones_from_layer(state.layers[-1])

        if support_zones:
            new_layer = Layer(
                z=current_actual_height(state.layers),
                height=orientation.height,
                placements=[],
            )

            positions = generate_top_positions_for_target(
                layer=new_layer,
                orientation=orientation,
                support_zones=support_zones,
                hard_length_limit=hard_length_limit,
                hard_width_limit=hard_width_limit,
                settings=settings,
            )

            for x, y in positions:
                new_layers = add_item_to_new_top_layer(
                    layers=state.layers,
                    item=item,
                    orientation=orientation,
                    x=x,
                    y=y,
                )

                plan = build_plan_from_layers(new_layers)

                if plan is not None:
                    next_states.append(PlanState(layers=new_layers))

    return next_states


def prune_beam(
    states: list[PlanState],
    settings: BeamSearchSettings,
) -> list[PlanState]:
    """
    Dedupes and keeps only the best beam states.
    """

    best_by_signature: dict[tuple, tuple[tuple, PlanState]] = {}

    for state in states:
        plan = build_plan_from_layers(state.layers)

        if plan is None:
            continue

        score = practical_plan_score(plan, settings)
        signature = state_signature(state.layers)

        current = best_by_signature.get(signature)

        if current is None or score < current[0]:
            best_by_signature[signature] = (score, state)

    scored_states = sorted(
        best_by_signature.values(),
        key=lambda value: value[0],
    )

    return [
        state
        for _, state in scored_states[: settings.beam_width]
    ]


def beam_search_pack(
    ordered_items: list[Item],
    settings: BeamSearchSettings,
    debug_label: str | None,
) -> SkidPlan | None:
    """
    Packs items with beam search.
    """

    attempt_start = time.perf_counter()
    group_text = f" for group {debug_label}" if debug_label else ""

    states = [PlanState(layers=[])]

    for item_number, item in enumerate(ordered_items, start=1):
        expanded_states = []

        for state in states:
            expanded_states.extend(
                generate_next_states(
                    state=state,
                    item=item,
                    settings=settings,
                )
            )

        states = prune_beam(
            states=expanded_states,
            settings=settings,
        )

        if not states:
            print_elapsed(
                (
                    f"{settings.name}{group_text}: failed after item "
                    f"{item_number}/{len(ordered_items)} "
                    f"(row {item.csv_row_number})."
                ),
                attempt_start_time=attempt_start,
            )
            return None

    best_plan = None
    best_score = None

    for state in states:
        plan = build_plan_from_layers(state.layers)

        if plan is None:
            continue

        plan = center_whole_plan_only(plan)

        if not plan_is_valid(plan):
            continue

        score = practical_plan_score(plan, settings)

        if best_score is None or score < best_score:
            best_score = score
            best_plan = plan

    if best_plan is None:
        print_elapsed(
            f"{settings.name}{group_text}: no complete valid plan.",
            attempt_start_time=attempt_start,
        )
        return None

    print_elapsed(
        (
            f"{settings.name}{group_text}: VALID skid "
            f"{round(best_plan.skid_length, 2)} x "
            f"{round(best_plan.skid_width, 2)} x "
            f"{round(best_plan.skid_height, 2)}."
        ),
        attempt_start_time=attempt_start,
    )

    return best_plan


def optimize_one_skid_for_all_items(items: list[Item], debug_label: str | None = None) -> SkidPlan | None:
    """
    Optimizes one group of cartons using practical beam search.

    This replaces the old greedy/fallback behavior.

    The optimizer tries multiple practical strategies:
    - biggest/flattest cartons first
    - long cartons first
    - similar-height grouping first
    - balanced score
    - smaller-skid score
    - lower-height score

    It keeps multiple candidate layouts at each step, so it can compare
    centering, stacking, and side-by-side placement instead of making one greedy
    decision too early.
    """

    group_text = f" for group {debug_label}" if debug_label else ""

    if not items:
        print_elapsed(f"No cartons found{group_text}; skipping group.")
        return None

    overall_start = time.perf_counter()

    print_elapsed(
        f"Starting practical optimization{group_text} with {len(items)} carton(s)."
    )

    item_orders = [
        ("biggest/flattest first", sorted(items, key=item_sort_key_big_flat, reverse=True)),
        ("longest cartons first", sorted(items, key=item_sort_key_long, reverse=True)),
        ("similar-height first", sorted(items, key=item_sort_key_height_group, reverse=True)),
    ]

    best_plan = None
    best_score = None
    attempts = 0

    for order_name, ordered_items in item_orders:
        for settings in PRACTICAL_SEARCH_SETTINGS:
            attempts += 1

            print_elapsed(
                f"Attempt {attempts}: {settings.name}{group_text}, order={order_name}."
            )

            plan = beam_search_pack(
                ordered_items=ordered_items,
                settings=settings,
                debug_label=debug_label,
            )

            if plan is None:
                continue

            score = practical_plan_score(plan, settings)

            # Final comparison uses balanced practical settings so all attempts
            # are judged consistently.
            balanced_score = practical_plan_score(
                plan,
                PRACTICAL_SEARCH_SETTINGS[0],
            )

            if best_score is None or balanced_score < best_score:
                best_score = balanced_score
                best_plan = plan

    if best_plan is not None:
        com_x, com_y, com_z = plan_center_of_mass(best_plan)

        print_elapsed(
            (
                f"Finished practical optimization{group_text}: BEST skid "
                f"{round(best_plan.skid_length, 2)} x "
                f"{round(best_plan.skid_width, 2)} x "
                f"{round(best_plan.skid_height, 2)}; "
                f"center of mass approx x={round(com_x, 2)}, "
                f"y={round(com_y, 2)}, z={round(com_z, 2)}."
            ),
            attempt_start_time=overall_start,
        )

        return best_plan

    if DEBUG_NO_VALID_SKID:
        print_no_valid_skid_diagnostics(items, debug_label=debug_label)

    print_elapsed(
        f"Finished practical optimization{group_text}: NO VALID SKID.",
        attempt_start_time=overall_start,
    )

    return None
