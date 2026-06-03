from dataclasses import dataclass, field
from itertools import permutations
import csv
import math
import sys


MAX_LOADED_HEIGHT = 87.52
MAX_SKID_WIDTH = 90.5

# Smaller = more accurate but slower. 0.5 is a good starting point.
WIDTH_STEP = 0.5

LENGTH_COL_OPTIONS = ["carton_length", "length", "l"]
WIDTH_COL_OPTIONS = ["carton_width", "width", "w"]
HEIGHT_COL_OPTIONS = ["carton_height", "height", "h"]
QUANTITY_COL_OPTIONS = ["quantity", "qty", "count"]

EPS = 1e-9


@dataclass(frozen=True)
class Orientation:
    length: float
    width: float
    height: float
    label: str


@dataclass(frozen=True)
class Carton:
    length: float
    width: float
    height: float

    @property
    def volume(self):
        return self.length * self.width * self.height

    def orientations(self):
        """
        Allows the carton to be placed in any orientation.

        Returned dimensions mean:
        - length: runs along skid length
        - width: runs across skid/container width
        - height: vertical stacked height
        """
        dims = [
            ("L", self.length),
            ("W", self.width),
            ("H", self.height),
        ]

        seen = set()

        for perm in permutations(dims, 3):
            l_axis, w_axis, h_axis = perm

            key = (
                round(l_axis[1], 6),
                round(w_axis[1], 6),
                round(h_axis[1], 6),
            )

            if key in seen:
                continue

            seen.add(key)

            yield Orientation(
                length=l_axis[1],
                width=w_axis[1],
                height=h_axis[1],
                label=(
                    f"{l_axis[0]} along skid length, "
                    f"{w_axis[0]} across skid width, "
                    f"{h_axis[0]} vertical"
                ),
            )


@dataclass(frozen=True)
class Item:
    csv_row_number: int
    copy_number: int
    carton: Carton


@dataclass
class Shelf:
    x: float
    used_width: float
    length: float


@dataclass(frozen=True)
class Position:
    shelf_index: int | None
    is_new_shelf: bool
    x: float
    y: float
    new_layer_length: float
    new_layer_width: float


@dataclass(frozen=True)
class Placement:
    csv_row_number: int
    copy_number: int
    x: float
    y: float
    z: float
    length: float
    width: float
    height: float
    orientation: str
    layer_number: int


@dataclass
class Layer:
    z: float
    height: float
    shelves: list[Shelf] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)

    @property
    def length(self):
        if not self.shelves:
            return 0
        return max(shelf.x + shelf.length for shelf in self.shelves)

    @property
    def width_used(self):
        if not self.shelves:
            return 0
        return max(shelf.used_width for shelf in self.shelves)

    def find_best_position(self, orientation: Orientation, max_width: float):
        if orientation.height > self.height + EPS:
            return None

        if orientation.width > max_width + EPS:
            return None

        current_length = self.length
        best_position = None
        best_score = None

        # Try placing inside existing shelves
        for i, shelf in enumerate(self.shelves):
            if shelf.used_width + orientation.width <= max_width + EPS:
                new_shelf_length = max(shelf.length, orientation.length)
                new_layer_length = max(
                    current_length,
                    shelf.x + new_shelf_length
                )
                new_layer_width = max(
                    self.width_used,
                    shelf.used_width + orientation.width
                )

                position = Position(
                    shelf_index=i,
                    is_new_shelf=False,
                    x=shelf.x,
                    y=shelf.used_width,
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

        # Try starting a new shelf
        if orientation.width <= max_width + EPS:
            new_layer_length = current_length + orientation.length
            new_layer_width = max(self.width_used, orientation.width)

            position = Position(
                shelf_index=None,
                is_new_shelf=True,
                x=current_length,
                y=0,
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

    def place(
        self,
        item: Item,
        orientation: Orientation,
        position: Position,
        layer_number: int,
    ):
        if position.is_new_shelf:
            self.shelves.append(
                Shelf(
                    x=position.x,
                    used_width=orientation.width,
                    length=orientation.length,
                )
            )
        else:
            shelf = self.shelves[position.shelf_index]
            shelf.used_width += orientation.width
            shelf.length = max(shelf.length, orientation.length)

        placement = Placement(
            csv_row_number=item.csv_row_number,
            copy_number=item.copy_number,
            x=position.x,
            y=position.y,
            z=self.z,
            length=orientation.length,
            width=orientation.width,
            height=orientation.height,
            orientation=orientation.label,
            layer_number=layer_number,
        )

        self.placements.append(placement)


@dataclass
class SkidPlan:
    skid_length: float
    skid_width: float
    skid_height: float
    layers: list[Layer]

    @property
    def area(self):
        return self.skid_length * self.skid_width

    @property
    def volume(self):
        return self.skid_length * self.skid_width * self.skid_height

    @property
    def placements(self):
        result = []
        for layer in self.layers:
            result.extend(layer.placements)
        return result


def find_column(fieldnames, options):
    normalized = {name.strip().lower(): name for name in fieldnames}

    for option in options:
        if option.lower() in normalized:
            return normalized[option.lower()]

    return None


def parse_float(value, row_number, column_name):
    if value is None or str(value).strip() == "":
        raise ValueError(f"Row {row_number}: missing value in '{column_name}'")

    try:
        number = float(value)
    except ValueError:
        raise ValueError(f"Row {row_number}: invalid number '{value}' in '{column_name}'")

    if number <= 0:
        raise ValueError(f"Row {row_number}: '{column_name}' must be greater than 0")

    return number


def parse_quantity(value, row_number, column_name):
    if value is None or str(value).strip() == "":
        return 1

    try:
        quantity = int(float(value))
    except ValueError:
        raise ValueError(f"Row {row_number}: invalid quantity '{value}' in '{column_name}'")

    if quantity < 1:
        raise ValueError(f"Row {row_number}: quantity must be at least 1")

    return quantity


def current_skid_length(layers):
    if not layers:
        return 0
    return max(layer.length for layer in layers)


def current_skid_width(layers):
    if not layers:
        return 0
    return max(layer.width_used for layer in layers)


def current_skid_height(layers):
    return sum(layer.height for layer in layers)


def pack_items_for_width(items, width_limit):
    layers = []

    # Pack biggest cartons first
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

        for orientation in item.carton.orientations():
            if orientation.width > width_limit + EPS:
                continue

            if orientation.height > MAX_LOADED_HEIGHT + EPS:
                continue

            # Try existing layers
            for layer_index, layer in enumerate(layers):
                position = layer.find_best_position(orientation, width_limit)

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

            # Try new layer
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

        if best_choice is None:
            return None

        choice_type, layer_index, orientation, position = best_choice

        if choice_type == "existing_layer":
            layer = layers[layer_index]
            layer.place(
                item=item,
                orientation=orientation,
                position=position,
                layer_number=layer_index + 1,
            )
        else:
            z = current_skid_height(layers)

            new_layer = Layer(
                z=z,
                height=orientation.height,
            )

            new_position = Position(
                shelf_index=None,
                is_new_shelf=True,
                x=0,
                y=0,
                new_layer_length=orientation.length,
                new_layer_width=orientation.width,
            )

            layers.append(new_layer)

            new_layer.place(
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


def generate_candidate_widths(items):
    lower_bound = 0

    for item in items:
        possible_widths = [
            orientation.width
            for orientation in item.carton.orientations()
            if orientation.height <= MAX_LOADED_HEIGHT + EPS
        ]

        if not possible_widths:
            return []

        lower_bound = max(lower_bound, min(possible_widths))

    if lower_bound > MAX_SKID_WIDTH + EPS:
        return []

    widths = set()
    widths.add(round(MAX_SKID_WIDTH, 4))

    # Add stepped widths
    start = math.ceil(lower_bound / WIDTH_STEP) * WIDTH_STEP
    width = start

    while width <= MAX_SKID_WIDTH + EPS:
        widths.add(round(width, 4))
        width += WIDTH_STEP

    # Add exact carton orientation widths too
    for item in items:
        for orientation in item.carton.orientations():
            if orientation.width <= MAX_SKID_WIDTH + EPS:
                widths.add(round(orientation.width, 4))

    return sorted(widths)


def optimize_one_skid_for_all_items(items):
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


def add_output_column(fieldnames, column_name):
    if column_name not in fieldnames:
        fieldnames.append(column_name)


def process_csv(input_path, output_path):
    with open(input_path, "r", newline="", encoding="utf-8-sig") as infile:
        reader = csv.DictReader(infile)

        if not reader.fieldnames:
            raise ValueError("CSV has no headers")

        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    length_col = find_column(fieldnames, LENGTH_COL_OPTIONS)
    width_col = find_column(fieldnames, WIDTH_COL_OPTIONS)
    height_col = find_column(fieldnames, HEIGHT_COL_OPTIONS)
    quantity_col = find_column(fieldnames, QUANTITY_COL_OPTIONS)

    missing = []

    if length_col is None:
        missing.append("carton_length / length")

    if width_col is None:
        missing.append("carton_width / width")

    if height_col is None:
        missing.append("carton_height / height")

    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    items = []

    for index, row in enumerate(rows):
        csv_row_number = index + 2

        carton = Carton(
            length=parse_float(row[length_col], csv_row_number, length_col),
            width=parse_float(row[width_col], csv_row_number, width_col),
            height=parse_float(row[height_col], csv_row_number, height_col),
        )

        quantity = (
            parse_quantity(row[quantity_col], csv_row_number, quantity_col)
            if quantity_col
            else 1
        )

        for copy_number in range(1, quantity + 1):
            items.append(
                Item(
                    csv_row_number=csv_row_number,
                    copy_number=copy_number,
                    carton=carton,
                )
            )

    if not items:
        raise ValueError("No cartons found in CSV")

    plan = optimize_one_skid_for_all_items(items)

    add_output_column(fieldnames, "skid_length")
    add_output_column(fieldnames, "skid_width")
    add_output_column(fieldnames, "skid_height")

    if plan is None:
        skid_length = "NO VALID SKID"
        skid_width = "NO VALID SKID"
        skid_height = "NO VALID SKID"

        print("No valid skid found for all cartons.")
    else:
        skid_length = round(plan.skid_length, 2)
        skid_width = round(plan.skid_width, 2)
        skid_height = round(plan.skid_height, 2)

        print("\nBest skid for ALL cartons")
        print("-------------------------")
        print(f"Total cartons packed: {len(items)}")
        print(f"Skid size: {skid_length} x {skid_width} x {skid_height}")
        print(f"Skid area: {round(plan.area, 2)}")
        print(f"Skid volume: {round(plan.volume, 2)}")
        print(f"Layers: {len(plan.layers)}")

        print("\nPlacement plan")
        print("--------------")
        for placement in plan.placements:
            print(
                f"CSV row {placement.csv_row_number}, "
                f"copy {placement.copy_number}: "
                f"layer {placement.layer_number}, "
                f"position x={round(placement.x, 2)}, "
                f"y={round(placement.y, 2)}, "
                f"z={round(placement.z, 2)}, "
                f"placed as {round(placement.length, 2)} x "
                f"{round(placement.width, 2)} x "
                f"{round(placement.height, 2)}, "
                f"{placement.orientation}"
            )

    # Same skid dimensions get written to every row because this is one combined skid.
    for row in rows:
        row["skid_length"] = skid_length
        row["skid_width"] = skid_width
        row["skid_height"] = skid_height

    with open(output_path, "w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Wrote output to: {output_path}")


def main():
    if len(sys.argv) != 3:
        print("Usage:")
        print("python skid_optimizer_all.py input.csv output.csv")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    process_csv(input_path, output_path)


if __name__ == "__main__":
    main()