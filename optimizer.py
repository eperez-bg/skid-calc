from __future__ import annotations

from itertools import permutations
import math

from models import (
    Carton,
    Orientation,
    Item,
    PackingStrip,
    Position,
    Placement,
    Layer,
    SkidPlan,
)


# ============================================================
# CONSTANTS
# ============================================================

# Maximum loaded skid height, including tolerance space.
MAX_LOADED_HEIGHT = 87.52

# Maximum skid width, including tolerance space.
# This is based on the container door/opening restriction.
MAX_SKID_WIDTH = 90.5

# Widths are tested in increments.
# Smaller = more accurate but slower.
WIDTH_STEP = 0.5

# Small tolerance for float comparisons.
EPS = 1e-9


# ============================================================
# ORIENTATION LOGIC
# ============================================================

def get_orientations(carton: Carton):
    """
    Generate all unique ways the carton can be oriented.

    A box can be turned in up to 6 ways.
    This function returns each unique orientation.
    """

    dims = [
        ("L", carton.length),
        ("W", carton.width),
        ("H", carton.height),
    ]

    seen = set()

    for perm in permutations(dims, 3):
        length_axis, width_axis, height_axis = perm

        key = (
            round(length_axis[1], 6),
            round(width_axis[1], 6),
            round(height_axis[1], 6),
        )

        if key in seen:
            continue

        seen.add(key)

        yield Orientation(
            length=length_axis[1],
            width=width_axis[1],
            height=height_axis[1],
            label=(
                f"{length_axis[0]} along skid length, "
                f"{width_axis[0]} across skid width, "
                f"{height_axis[0]} vertical"
            ),
        )


# ============================================================
# SKID MEASUREMENT HELPERS
# ============================================================

def current_skid_length(layers: list[Layer]) -> float:
    """
    The skid length is the largest length used by any layer.
    """
    if not layers:
        return 0.0

    return max(layer.length for layer in layers)


def current_skid_width(layers: list[Layer]) -> float:
    """
    The skid width is the largest width used by any layer.
    """
    if not layers:
        return 0.0

    return max(layer.width_used for layer in layers)


def current_skid_height(layers: list[Layer]) -> float:
    """
    Total stacked height is the sum of all layer heights.
    """
    return sum(layer.height for layer in layers)


# ============================================================
# LAYER / STRIP PLACEMENT LOGIC
# ============================================================

def find_best_position_in_layer(
    layer: Layer,
    orientation: Orientation,
    width_limit: float,
) -> Position | None:
    """
    Try to find the best place for this carton orientation inside one layer.

    The function tries:
    1. Put the carton into an existing strip.
    2. Start a new strip.

    Returns the best Position, or None if it cannot fit.
    """

    # If the carton is taller than this layer, it cannot go here.
    if orientation.height > layer.height + EPS:
        return None

    # If the carton is wider than the allowed skid width, it cannot fit.
    if orientation.width > width_limit + EPS:
        return None

    current_length = layer.length
    best_position = None
    best_score = None

    # ------------------------------------------------------------
    # Option 1: Try existing strips
    # ------------------------------------------------------------
    for index, strip in enumerate(layer.strips):

        # Check if there is enough remaining width inside this strip.
        if strip.used_width + orientation.width <= width_limit + EPS:

            # The strip length may increase if this carton is longer.
            new_strip_length = max(strip.length, orientation.length)

            # The layer length may increase if this strip gets longer.
            new_layer_length = max(
                current_length,
                strip.x + new_strip_length,
            )

            # The layer width may increase if the strip uses more width.
            new_layer_width = max(
                layer.width_used,
                strip.used_width + orientation.width,
            )

            position = Position(
                strip_index=index,
                is_new_strip=False,
                x=strip.x,
                y=strip.used_width,
                new_layer_length=new_layer_length,
                new_layer_width=new_layer_width,
            )

            # Lower score is better.
            # Prioritize smaller footprint for the layer.
            score = (
                new_layer_length * new_layer_width,
                new_layer_length,
                new_layer_width,
            )

            if best_score is None or score < best_score:
                best_score = score
                best_position = position

    # ------------------------------------------------------------
    # Option 2: Start a new strip
    # ------------------------------------------------------------

    new_layer_length = current_length + orientation.length
    new_layer_width = max(layer.width_used, orientation.width)

    position = Position(
        strip_index=None,
        is_new_strip=True,
        x=current_length,
        y=0.0,
        new_layer_length=new_layer_length,
        new_layer_width=new_layer_width,
    )

    score = (
        new_layer_length * new_layer_width,
        new_layer_length,
        new_layer_width,
    )

    if best_score is None or score < best_score:
        best_score = score
        best_position = position

    return best_position


def place_item_in_layer(
    layer: Layer,
    item: Item,
    orientation: Orientation,
    position: Position,
    layer_number: int,
) -> None:
    """
    Actually places an item into a layer.

    This updates the layer's strips and records a Placement.
    """

    if position.is_new_strip:
        layer.strips.append(
            PackingStrip(
                x=position.x,
                used_width=orientation.width,
                length=orientation.length,
            )
        )
    else:
        strip = layer.strips[position.strip_index]
        strip.used_width += orientation.width
        strip.length = max(strip.length, orientation.length)

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
# PACKING LOGIC
# ============================================================

def pack_items_for_width(
    items: list[Item],
    width_limit: float,
) -> SkidPlan | None:
    """
    Try to pack all cartons using a specific max skid width.

    The main optimizer will call this several times with different
    width limits and keep the best result.
    """

    layers: list[Layer] = []

    # Bigger cartons get packed first.
    # This usually gives better results for greedy packing.
    sorted_items = sorted(
        items,
        key=lambda item: (
            item.carton.volume,
            max(item.carton.length, item.carton.width, item.carton.height),
        ),
        reverse=True,
    )

    for item in sorted_items:
        best_choice = None
        best_score = None

        current_length = current_skid_length(layers)
        current_width = current_skid_width(layers)
        current_height = current_skid_height(layers)

        for orientation in get_orientations(item.carton):

            # Reject impossible orientations.
            if orientation.width > width_limit + EPS:
                continue

            if orientation.height > MAX_LOADED_HEIGHT + EPS:
                continue

            # --------------------------------------------------------
            # Try placing into existing layers
            # --------------------------------------------------------
            for layer_index, layer in enumerate(layers):

                position = find_best_position_in_layer(
                    layer=layer,
                    orientation=orientation,
                    width_limit=width_limit,
                )

                if position is None:
                    continue

                new_length = max(current_length, position.new_layer_length)
                new_width = max(current_width, position.new_layer_width)
                new_height = current_height

                score = (
                    new_length * new_width,
                    new_height,
                    new_length,
                    new_width,
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
            # Try creating a new layer
            # --------------------------------------------------------
            if current_height + orientation.height <= MAX_LOADED_HEIGHT + EPS:

                new_length = max(current_length, orientation.length)
                new_width = max(current_width, orientation.width)
                new_height = current_height + orientation.height

                score = (
                    new_length * new_width,
                    new_height,
                    new_length,
                    new_width,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_choice = (
                        "new_layer",
                        None,
                        orientation,
                        None,
                    )

        # If the item cannot fit anywhere, this width limit fails.
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
            # New layer starts at current total height.
            z = current_skid_height(layers)

            new_layer = Layer(
                z=z,
                height=orientation.height,
            )

            # First item in a new layer starts at x=0, y=0.
            new_position = Position(
                strip_index=None,
                is_new_strip=True,
                x=0.0,
                y=0.0,
                new_layer_length=orientation.length,
                new_layer_width=orientation.width,
            )

            layers.append(new_layer)

            place_item_in_layer(
                layer=new_layer,
                item=item,
                orientation=orientation,
                position=new_position,
                layer_number=len(layers),
            )

    skid_length = current_skid_length(layers)
    skid_width = current_skid_width(layers)
    skid_height = current_skid_height(layers)

    if skid_width > MAX_SKID_WIDTH + EPS:
        return None

    if skid_height > MAX_LOADED_HEIGHT + EPS:
        return None

    return SkidPlan(
        skid_length=skid_length,
        skid_width=skid_width,
        skid_height=skid_height,
        layers=layers,
    )


def generate_candidate_widths(items: list[Item]) -> list[float]:
    """
    Generate the skid widths that the optimizer will test.

    Example:
    If WIDTH_STEP = 0.5, it tests widths like:
    30.0, 30.5, 31.0, ..., 90.5

    It also tests exact carton widths because sometimes the best answer
    happens at an exact carton dimension.
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

    if lower_bound > MAX_SKID_WIDTH + EPS:
        return []

    widths = set()

    # Always test the max allowed width.
    widths.add(round(MAX_SKID_WIDTH, 4))

    # Add stepped width values.
    start = math.ceil(lower_bound / WIDTH_STEP) * WIDTH_STEP
    width = start

    while width <= MAX_SKID_WIDTH + EPS:
        widths.add(round(width, 4))
        width += WIDTH_STEP

    # Add exact orientation widths.
    for item in items:
        for orientation in get_orientations(item.carton):
            if orientation.width <= MAX_SKID_WIDTH + EPS:
                widths.add(round(orientation.width, 4))

    return sorted(widths)


def optimize_one_skid_for_all_items(items: list[Item]) -> SkidPlan | None:
    """
    Finds one skid size that fits all cartons together.

    It tries many possible skid widths.
    For each width, it tries to pack all cartons.
    Then it chooses the best successful plan.

    Best means:
    1. Smallest skid footprint area
    2. Smallest loaded volume
    3. Smallest length
    4. Smallest width
    5. Smallest height
    """

    candidate_widths = generate_candidate_widths(items)

    best_plan = None
    best_score = None

    for width_limit in candidate_widths:
        plan = pack_items_for_width(items, width_limit)

        if plan is None:
            continue

        score = (
            plan.area,
            plan.volume,
            plan.skid_length,
            plan.skid_width,
            plan.skid_height,
        )

        if best_score is None or score < best_score:
            best_score = score
            best_plan = plan

    return best_plan