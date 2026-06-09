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
# CONSTANTS
# ============================================================

MAX_LOADED_HEIGHT = 87.52
MAX_SKID_WIDTH = 90.5

LENGTH_CLEARANCE = 1.97
WIDTH_CLEARANCE = 1.18

MIN_OUTPUT_SKID_HEIGHT = 47.24

WIDTH_STEP = 0.5
EPS = 1e-9

MAX_INTERNAL_CARTON_WIDTH = MAX_SKID_WIDTH - WIDTH_CLEARANCE


# ============================================================
# BALANCED SUPPORT SETTINGS
# ============================================================

# At least this much of the carton bottom must be supported.
# 0.90 = 90% supported.
MIN_SUPPORT_RATIO = 0.90

# Maximum allowed overhang on any one side.
# 1.0 means no side can hang over support by more than 1 inch.
MAX_SIDE_OVERHANG = 1.0

# This affects scoring.
# Higher number means the optimizer will avoid overhang more aggressively.
OVERHANG_SCORE_WEIGHT = 1000


# ============================================================
# TEMPORARY GEOMETRY MODELS
# ============================================================

@dataclass(frozen=True)
class Rect:
    """
    Simple 2D rectangle used for support checking.
    """

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
    """
    A possible x/y location inside a layer.
    """

    x: float
    y: float
    new_layer_length: float
    new_layer_width: float
    support_ratio: float
    side_overhang: float


# ============================================================
# ORIENTATION LOGIC
# ============================================================

def get_orientations(carton: Carton):
    """
    Allows flat rotation only.

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
# BASIC MEASUREMENT HELPERS
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


# ============================================================
# RECTANGLE / COLLISION HELPERS
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
    """
    Returns True if this rectangle overlaps anything already in the layer.
    """

    for placement in layer.placements:
        if rectangles_overlap(x, y, length, width, placement):
            return True

    return False


def intersection_rect(a: Rect, b: Rect) -> tuple[float, float, float, float] | None:
    """
    Returns the intersection rectangle as x1, y1, x2, y2.

    Returns None if the rectangles do not overlap.
    """

    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x2, b.x2)
    y2 = min(a.y2, b.y2)

    if x2 <= x1 + EPS or y2 <= y1 + EPS:
        return None

    return x1, y1, x2, y2


def union_area(rects: list[tuple[float, float, float, float]]) -> float:
    """
    Calculates the union area of overlapping rectangles.

    This prevents double-counting support area when two support rectangles overlap.
    """

    if not rects:
        return 0.0

    xs = sorted(set([r[0] for r in rects] + [r[2] for r in rects]))

    area = 0.0

    for i in range(len(xs) - 1):
        x_left = xs[i]
        x_right = xs[i + 1]

        if x_right <= x_left + EPS:
            continue

        active_y_intervals = []

        for rx1, ry1, rx2, ry2 in rects:
            if rx1 <= x_left + EPS and rx2 >= x_right - EPS:
                active_y_intervals.append((ry1, ry2))

        if not active_y_intervals:
            continue

        active_y_intervals.sort()

        merged = []
        current_start, current_end = active_y_intervals[0]

        for start, end in active_y_intervals[1:]:
            if start <= current_end + EPS:
                current_end = max(current_end, end)
            else:
                merged.append((current_start, current_end))
                current_start, current_end = start, end

        merged.append((current_start, current_end))

        covered_y = sum(end - start for start, end in merged)
        area += (x_right - x_left) * covered_y

    return area


# ============================================================
# SUPPORT CHECKING
# ============================================================

def get_support_zones_from_layer(layer: Layer) -> list[Rect]:
    """
    Builds support rectangles from a layer.

    Only cartons that reach the layer's full height can support cartons above.
    """

    zones = []

    for placement in layer.placements:
        if placement.height + EPS >= layer.height:
            zones.append(
                Rect(
                    x=placement.x,
                    y=placement.y,
                    length=placement.length,
                    width=placement.width,
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
    Checks if a carton is supported enough.

    Bottom layer:
        Always supported by the skid.

    Upper layer:
        Must be supported by cartons underneath.

    Returns:
        valid, support_ratio, max_side_overhang
    """

    # Bottom layer is supported by the skid itself.
    if support_zones is None:
        return True, 1.0, 0.0

    top_rect = Rect(x=x, y=y, length=length, width=width)

    intersections = []
    overlapping_zones = []

    for zone in support_zones:
        intersection = intersection_rect(top_rect, zone)

        if intersection is not None:
            intersections.append(intersection)
            overlapping_zones.append(zone)

    if not intersections:
        return False, 0.0, float("inf")

    supported_area = union_area(intersections)
    support_ratio = supported_area / top_rect.area

    # Build a bounding box around all support zones that touch this carton.
    support_min_x = min(zone.x for zone in overlapping_zones)
    support_max_x = max(zone.x2 for zone in overlapping_zones)
    support_min_y = min(zone.y for zone in overlapping_zones)
    support_max_y = max(zone.y2 for zone in overlapping_zones)

    left_overhang = max(0.0, support_min_x - top_rect.x)
    right_overhang = max(0.0, top_rect.x2 - support_max_x)
    front_overhang = max(0.0, support_min_y - top_rect.y)
    back_overhang = max(0.0, top_rect.y2 - support_max_y)

    max_side_overhang = max(
        left_overhang,
        right_overhang,
        front_overhang,
        back_overhang,
    )

    valid = (
        support_ratio >= MIN_SUPPORT_RATIO - EPS and
        max_side_overhang <= MAX_SIDE_OVERHANG + EPS
    )

    return valid, support_ratio, max_side_overhang


# ============================================================
# POSITION GENERATION
# ============================================================

def candidate_xy_points(
    layer: Layer,
    orientation: Orientation,
    support_zones: list[Rect] | None,
) -> set[tuple[float, float]]:
    """
    Generates candidate x/y positions.

    We do not test every possible decimal coordinate.
    Instead, we test useful places:
        - origin
        - right side of existing cartons
        - behind existing cartons
        - corners of support zones
    """

    points = {(0.0, 0.0)}

    # Points based on cartons already in this layer.
    for placement in layer.placements:
        points.add((placement.x + placement.length, placement.y))
        points.add((placement.x, placement.y + placement.width))

    # Points based on support zones from below.
    if support_zones is not None:
        for zone in support_zones:
            points.add((zone.x, zone.y))

            # Align right edge of carton to right edge of support zone.
            points.add((zone.x2 - orientation.length, zone.y))

            # Align back edge of carton to back edge of support zone.
            points.add((zone.x, zone.y2 - orientation.width))

            # Align both right/back edges.
            points.add((zone.x2 - orientation.length, zone.y2 - orientation.width))

            # Align carton centered on the support zone (both axes).
            center_x = zone.x + (zone.length - orientation.length) / 2.0
            center_y = zone.y + (zone.width - orientation.width) / 2.0
            points.add((center_x, center_y))

            # Allow small overhang around the zone.
            points.add((zone.x - MAX_SIDE_OVERHANG, zone.y))
            points.add((zone.x, zone.y - MAX_SIDE_OVERHANG))
            points.add((zone.x2 - orientation.length + MAX_SIDE_OVERHANG, zone.y))
            points.add((zone.x, zone.y2 - orientation.width + MAX_SIDE_OVERHANG))

    # Remove negative points.
    cleaned = set()

    for x, y in points:
        if x >= -EPS and y >= -EPS:
            cleaned.add((max(0.0, x), max(0.0, y)))

    return cleaned


def find_best_position_in_layer(
    layer: Layer,
    orientation: Orientation,
    width_limit: float,
    support_zones: list[Rect] | None,
) -> CandidatePosition | None:
    """
    Finds the best x/y position in this layer for this orientation.
    """

    if orientation.height > layer.height + EPS:
        return None

    best_position = None
    best_score = None

    for x, y in candidate_xy_points(layer, orientation, support_zones):
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

        position = CandidatePosition(
            x=x,
            y=y,
            new_layer_length=new_layer_length,
            new_layer_width=new_layer_width,
            support_ratio=support_ratio,
            side_overhang=side_overhang,
        )

        # Lower score is better.
        # Prefer compact layer usage, then less overhang, then more support.
        score = (
            new_layer_length * new_layer_width,
            side_overhang * OVERHANG_SCORE_WEIGHT,
            -support_ratio,
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
    """
    Actually places the item into the layer.
    """

    placement = Placement(
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

    layer.placements.append(placement)


# ============================================================
# SAFE CENTERING / POST-PROCESSING
# ============================================================

def get_layer_bounds(layer: Layer) -> tuple[float, float, float, float] | None:
    """
    Returns the bounding box of one layer.

    Returns:
        min_x, max_x, min_y, max_y
    """

    if not layer.placements:
        return None

    min_x = min(p.x for p in layer.placements)
    max_x = max(p.x + p.length for p in layer.placements)

    min_y = min(p.y for p in layer.placements)
    max_y = max(p.y + p.width for p in layer.placements)

    return min_x, max_x, min_y, max_y


def get_plan_bounds(plan: SkidPlan) -> tuple[float, float, float, float] | None:
    """
    Returns the bounding box of the whole packed load.
    """

    if not plan.placements:
        return None

    min_x = min(p.x for p in plan.placements)
    max_x = max(p.x + p.length for p in plan.placements)

    min_y = min(p.y for p in plan.placements)
    max_y = max(p.y + p.width for p in plan.placements)

    return min_x, max_x, min_y, max_y


def shift_layer(layer: Layer, dx: float, dy: float) -> Layer:
    """
    Shifts every placement in one layer by dx/dy.
    """

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
        strips=[],
        placements=shifted_placements,
    )


def shift_layers_from_index(
    layers: list[Layer],
    start_index: int,
    dx: float,
    dy: float,
) -> list[Layer]:
    """
    Shifts one layer and every layer above it.

    This is important:
    If we shift layer 3, we also shift layers 4, 5, etc.
    That preserves the support relationship between upper layers.
    """

    shifted_layers = []

    for index, layer in enumerate(layers):
        if index >= start_index:
            shifted_layers.append(shift_layer(layer, dx, dy))
        else:
            shifted_layers.append(layer)

    return shifted_layers


def layer_has_collisions(layer: Layer) -> bool:
    """
    Checks whether any cartons overlap inside the same layer.
    """

    placements = layer.placements

    for i in range(len(placements)):
        a = placements[i]

        for j in range(i + 1, len(placements)):
            b = placements[j]

            if rectangles_overlap(
                x=a.x,
                y=a.y,
                length=a.length,
                width=a.width,
                placement=b,
            ):
                return True

    return False


def plan_is_valid(plan: SkidPlan) -> bool:
    """
    Validates a plan after centering.

    Checks:
    - no negative x/y
    - no carton outside skid dimensions
    - no same-layer collisions
    - every upper layer is still supported by the layer below
    """

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
        below_layer = plan.layers[layer_index - 1]
        upper_layer = plan.layers[layer_index]

        support_zones = get_support_zones_from_layer(below_layer)

        if not support_zones:
            return False

        for p in upper_layer.placements:
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
    """
    Creates a new SkidPlan with the same skid dimensions but new layer placements.
    """

    return SkidPlan(
        skid_length=plan.skid_length,
        skid_width=plan.skid_width,
        skid_height=plan.skid_height,
        layers=layers,
    )


def center_whole_plan_on_skid(plan: SkidPlan) -> SkidPlan:
    """
    Centers the entire packed load on the skid as one unit.

    This preserves all support relationships because every layer moves together.
    """

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
    """
    Tries to center each upper layer over the layer below it.

    Important:
    When shifting layer i, it also shifts every layer above i.
    This keeps the stack above that layer together.

    A shift is only accepted if the whole plan remains valid.
    """

    current_plan = plan

    for layer_index in range(1, len(current_plan.layers)):
        below_layer = current_plan.layers[layer_index - 1]
        current_layer = current_plan.layers[layer_index]

        below_bounds = get_layer_bounds(below_layer)
        current_bounds = get_layer_bounds(current_layer)

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
    """
    Final centering pass.

    Step 1:
        Center the entire stack on the skid.

    Step 2:
        Try to center upper layers over their supporting layer.

    Every move is validated before being accepted.
    """

    centered = center_whole_plan_on_skid(plan)
    centered = center_upper_layers_safely(centered)

    return centered


# ============================================================
# PACKING FOR ONE WIDTH LIMIT
# ============================================================

def pack_items_for_width(
    items: list[Item],
    width_limit: float,
) -> SkidPlan | None:
    """
    Tries to pack all cartons using one internal width limit.

    This is the balanced version:
        - Uses x/y layer packing
        - Allows small overhang
        - Allows support from multiple cartons below
        - Prevents large unsupported floating cartons
    """

    layers: list[Layer] = []

    # Larger cartons first.
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

            if orientation.height > MAX_LOADED_HEIGHT + EPS:
                continue

            # --------------------------------------------------------
            # Option 1: Existing layers
            # --------------------------------------------------------
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
                    support_zones=support_zones,
                )

                if position is None:
                    continue

                new_internal_length = max(current_length, position.new_layer_length)
                new_internal_width = max(current_width, position.new_layer_width)
                new_actual_height = current_height

                final_length = new_internal_length + LENGTH_CLEARANCE
                final_width = new_internal_width + WIDTH_CLEARANCE
                final_height = max(MIN_OUTPUT_SKID_HEIGHT, new_actual_height)

                if final_width > MAX_SKID_WIDTH + EPS:
                    continue

                if new_actual_height > MAX_LOADED_HEIGHT + EPS:
                    continue

                score = (
                    final_length * final_width,
                    new_actual_height,
                    position.side_overhang * OVERHANG_SCORE_WEIGHT,
                    -position.support_ratio,
                    final_length,
                    final_width,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_choice = (
                        "existing_layer",
                        layer_index,
                        orientation,
                        position,
                    )

            # --------------------------------------------------------
            # Option 2: New layer
            # --------------------------------------------------------
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

                new_layer = Layer(
                    z=z,
                    height=orientation.height,
                    strips=[],
                    placements=[],
                )

                position = find_best_position_in_layer(
                    layer=new_layer,
                    orientation=orientation,
                    width_limit=width_limit,
                    support_zones=support_zones,
                )

                if position is None:
                    continue

                new_internal_length = max(current_length, position.new_layer_length)
                new_internal_width = max(current_width, position.new_layer_width)

                final_length = new_internal_length + LENGTH_CLEARANCE
                final_width = new_internal_width + WIDTH_CLEARANCE
                final_height = max(MIN_OUTPUT_SKID_HEIGHT, new_actual_height)

                if final_width > MAX_SKID_WIDTH + EPS:
                    continue

                score = (
                    final_length * final_width,
                    new_actual_height,
                    position.side_overhang * OVERHANG_SCORE_WEIGHT,
                    -position.support_ratio,
                    final_length,
                    final_width,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_choice = (
                        "new_layer",
                        None,
                        orientation,
                        position,
                    )

        if best_choice is None:
            return None

        choice_type, layer_index, orientation, position = best_choice

        if choice_type == "existing_layer":
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

            new_layer = Layer(
                z=z,
                height=orientation.height,
                strips=[],
                placements=[],
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

    skid_length = internal_length + LENGTH_CLEARANCE
    skid_width = internal_width + WIDTH_CLEARANCE
    skid_height = max(MIN_OUTPUT_SKID_HEIGHT, actual_height)

    if skid_width > MAX_SKID_WIDTH + EPS:
        return None

    if actual_height > MAX_LOADED_HEIGHT + EPS:
        return None

    raw_plan = SkidPlan(
        skid_length=skid_length,
        skid_width=skid_width,
        skid_height=skid_height,
        layers=layers,
    )

    return center_plan_on_skid(raw_plan)


# ============================================================
# WIDTH CANDIDATES
# ============================================================

def generate_candidate_widths(items: list[Item]) -> list[float]:
    """
    Generates internal skid-width limits to test.
    """

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

    if lower_bound > MAX_INTERNAL_CARTON_WIDTH + EPS:
        return []

    widths = set()

    widths.add(round(MAX_INTERNAL_CARTON_WIDTH, 4))

    start = math.ceil(lower_bound / WIDTH_STEP) * WIDTH_STEP
    width = start

    while width <= MAX_INTERNAL_CARTON_WIDTH + EPS:
        widths.add(round(width, 4))
        width += WIDTH_STEP

    for item in items:
        for orientation in get_orientations(item.carton):
            if orientation.width <= MAX_INTERNAL_CARTON_WIDTH + EPS:
                widths.add(round(orientation.width, 4))

    return sorted(widths)


# ============================================================
# MAIN OPTIMIZER
# ============================================================

def optimize_one_skid_for_all_items(items: list[Item]) -> SkidPlan | None:
    """
    Finds one skid size that fits all cartons.

    Best means:
        1. Smallest skid footprint
        2. Smaller actual height
        3. Less overhang
        4. Smaller length/width
    """

    if not items:
        return None

    best_plan = None
    best_score = None

    for width_limit in generate_candidate_widths(items):
        plan = pack_items_for_width(items, width_limit)

        if plan is None:
            continue

        actual_height = current_actual_height(plan.layers)

        # Calculate total overhang warning score for final plan.
        total_overhang_score = 0.0

        for layer_index in range(1, len(plan.layers)):
            support_zones = get_support_zones_from_layer(plan.layers[layer_index - 1])

            for placement in plan.layers[layer_index].placements:
                valid, support_ratio, side_overhang = check_support(
                    x=placement.x,
                    y=placement.y,
                    length=placement.length,
                    width=placement.width,
                    support_zones=support_zones,
                )

                total_overhang_score += side_overhang
                total_overhang_score += max(0.0, MIN_SUPPORT_RATIO - support_ratio) * 100

        score = (
            plan.area,
            actual_height,
            total_overhang_score * OVERHANG_SCORE_WEIGHT,
            plan.skid_length,
            plan.skid_width,
            plan.skid_height,
        )

        if best_score is None or score < best_score:
            best_score = score
            best_plan = plan

    return best_plan