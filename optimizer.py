from __future__ import annotations

from dataclasses import dataclass
import math

from models import (
    Carton,
    Orientation,
    Item,
    Placement,
    Layer,
    SkidPlan,
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

MIN_OUTPUT_SKID_HEIGHT = 47.24

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

# Scoring weights.
OVERHANG_SCORE_WEIGHT = 3000.0
SUPPORT_WASTE_WEIGHT = 0.10
CENTER_OFFSET_WEIGHT = 20.0
HEIGHT_WEIGHT = 75.0


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

    Only cartons that reach the full layer height are treated as support.
    """

    zones = []

    for p in layer.placements:
        if p.height + EPS >= layer.height:
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
    """

    if orientation.height > layer.height + EPS:
        return False

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
        )

        score = (
            side_overhang * OVERHANG_SCORE_WEIGHT,
            -support_ratio,
            waste * SUPPORT_WASTE_WEIGHT,
            center_offset * CENTER_OFFSET_WEIGHT,
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
                new_actual_height = current_height

                skid_l = final_skid_length(new_internal_length)
                skid_w = final_skid_width(new_internal_width)

                if not is_within_skid_limits(skid_l, skid_w):
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
    skid_h = max(MIN_OUTPUT_SKID_HEIGHT, actual_height)

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


def optimize_one_skid_for_all_items(items: list[Item]) -> SkidPlan | None:
    if not items:
        return None

    best_plan = None
    best_score = None

    length_limit = max_internal_length()

    for width_limit in generate_candidate_widths(items):
        plan = pack_items_for_width(
            items=items,
            width_limit=width_limit,
            length_limit=length_limit,
        )

        if plan is None:
            continue

        actual_height = current_actual_height(plan.layers)
        total_overhang, support_loss, waste = plan_support_score(plan)

        score = (
            plan.area,
            actual_height * HEIGHT_WEIGHT,
            total_overhang * OVERHANG_SCORE_WEIGHT,
            support_loss * OVERHANG_SCORE_WEIGHT,
            waste * SUPPORT_WASTE_WEIGHT,
            plan.skid_length,
            plan.skid_width,
            plan.skid_height,
        )

        if best_score is None or score < best_score:
            best_score = score
            best_plan = plan

    return best_plan