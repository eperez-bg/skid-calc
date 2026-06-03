from dataclasses import dataclass, field
from itertools import permutations
import csv
import math
import sys


# ============================================================
# CONSTANT RULES / LIMITS
# ============================================================

# Maximum loaded skid height.
# This includes whatever tolerance/clearance space you already decided to keep.
MAX_LOADED_HEIGHT = 87.52

# Maximum skid width.
# This is based on the container door/opening restriction.
# This also already includes tolerance/clearance.
MAX_SKID_WIDTH = 90.5

# The algorithm tests different possible skid widths.
# Smaller step = more accurate, but slower.
# Example: 0.5 means it tests 40.0, 40.5, 41.0, etc.
WIDTH_STEP = 0.5

# Tiny number used to prevent weird decimal comparison issues.
# Example: Python might calculate 90.5000000001 instead of 90.5.
EPS = 1e-9


# ============================================================
# CSV COLUMN NAME OPTIONS
# ============================================================

# These are possible column names the script will accept.
# So your CSV can use "length" or "carton_length" or "l".
LENGTH_COL_OPTIONS = ["carton_length", "length", "l"]
WIDTH_COL_OPTIONS = ["carton_width", "width", "w"]
HEIGHT_COL_OPTIONS = ["carton_height", "height", "h"]

# Quantity is optional.
# If there is no quantity column, each row is treated as quantity 1.
QUANTITY_COL_OPTIONS = ["quantity", "qty", "count"]


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass(frozen=True)
class Orientation:
    """
    Represents one possible way to place a carton.

    For example, a carton with dimensions:
        Length = 77.95
        Width  = 37.01
        Height = 2.76

    Could be placed as:
        77.95 along skid length
        37.01 across skid width
        2.76 vertical

    Or rotated differently:
        37.01 along skid length
        2.76 across skid width
        77.95 vertical

    This class stores one of those possible orientations.
    """

    length: float  # Dimension running along the skid length
    width: float   # Dimension running across the skid width
    height: float  # Dimension pointing vertically upward
    label: str     # Human-readable explanation of the orientation


@dataclass(frozen=True)
class Carton:
    """
    Represents the physical size of one carton type.

    This does not represent quantity yet.
    Quantity is handled later by creating multiple Item objects.
    """

    length: float
    width: float
    height: float

    @property
    def volume(self):
        """
        Returns the carton volume.

        This is used to sort cartons so that larger cartons are packed first.
        Packing larger objects first usually gives better results.
        """
        return self.length * self.width * self.height

    def orientations(self):
        """
        Generates all unique ways this carton can be oriented.

        A rectangular carton has up to 6 possible orientations:
            L x W base, H vertical
            L x H base, W vertical
            W x L base, H vertical
            W x H base, L vertical
            H x L base, W vertical
            H x W base, L vertical

        Some cartons may have repeated dimensions, so duplicates are removed.
        """

        # Store each dimension with a label so we can explain the orientation later.
        dims = [
            ("L", self.length),
            ("W", self.width),
            ("H", self.height),
        ]

        # Used to avoid duplicate orientations.
        # Example: if width and height are both 10, some permutations are identical.
        seen = set()

        # permutations(dims, 3) tries every possible order of the 3 dimensions.
        for perm in permutations(dims, 3):
            l_axis, w_axis, h_axis = perm

            # Key is the numeric orientation.
            # Rounding helps avoid tiny floating point differences.
            key = (
                round(l_axis[1], 6),
                round(w_axis[1], 6),
                round(h_axis[1], 6),
            )

            # Skip duplicate orientation.
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
    """
    Represents one physical carton that needs to be packed.

    If a CSV row has quantity 5, we create 5 Item objects.

    csv_row_number is stored so we can trace each packed carton
    back to the original CSV row.
    """

    csv_row_number: int
    copy_number: int
    carton: Carton


@dataclass
class Shelf:
    """
    Represents one 'shelf' inside a layer.

    Think of a layer as the top-down view of the skid.

    This simple packing algorithm places cartons into vertical strips/shelves.

    x:
        Where this shelf starts along the skid length.

    used_width:
        How much width has been used inside this shelf.

    length:
        The longest carton length in this shelf.
    """

    x: float
    used_width: float
    length: float


@dataclass(frozen=True)
class Position:
    """
    Represents a possible location where a carton could be placed.

    This does not actually place the carton yet.
    It just describes a candidate position.

    shelf_index:
        Which shelf the carton would go into.
        None means it would start a brand-new shelf.

    is_new_shelf:
        True if this position creates a new shelf.

    x, y:
        Top-down position on the skid.

    new_layer_length:
        What the layer length would become after placing the carton.

    new_layer_width:
        What the layer width would become after placing the carton.
    """

    shelf_index: int | None
    is_new_shelf: bool
    x: float
    y: float
    new_layer_length: float
    new_layer_width: float


@dataclass(frozen=True)
class Placement:
    """
    Represents a carton after it has been placed.

    This is useful for printing a loading plan.

    x, y, z:
        Position on the skid.

    length, width, height:
        The carton dimensions in the chosen orientation.

    orientation:
        Human-readable orientation instructions.

    layer_number:
        Which vertical layer this carton is on.
    """

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
    """
    Represents one vertical layer of cartons.

    A skid may have multiple layers stacked upward.

    Example:
        Layer 1 starts at z = 0
        Layer 2 starts at z = 2.76
        Layer 3 starts at z = 5.52

    Each layer has a fixed height.
    Cartons placed into that layer must have vertical height
    less than or equal to the layer height.
    """

    z: float
    height: float
    shelves: list[Shelf] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)

    @property
    def length(self):
        """
        Current length used by this layer.

        We calculate it by checking the furthest point reached
        by any shelf.
        """
        if not self.shelves:
            return 0

        return max(shelf.x + shelf.length for shelf in self.shelves)

    @property
    def width_used(self):
        """
        Current width used by this layer.

        We calculate it by checking the widest shelf usage.
        """
        if not self.shelves:
            return 0

        return max(shelf.used_width for shelf in self.shelves)

    def find_best_position(self, orientation: Orientation, max_width: float):
        """
        Tries to find the best place for a carton inside this layer.

        It tries two things:
            1. Put the carton inside an existing shelf
            2. Start a new shelf

        It returns the position that creates the smallest layer footprint.
        """

        # Carton cannot go into this layer if it is taller than the layer.
        if orientation.height > self.height + EPS:
            return None

        # Carton cannot fit if its width alone exceeds max allowed skid width.
        if orientation.width > max_width + EPS:
            return None

        current_length = self.length
        best_position = None
        best_score = None

        # ------------------------------------------------------------
        # Option 1: Try placing carton inside existing shelves
        # ------------------------------------------------------------
        for i, shelf in enumerate(self.shelves):

            # Check whether this shelf has enough remaining width.
            if shelf.used_width + orientation.width <= max_width + EPS:

                # If this carton is longer than the shelf's current length,
                # the shelf length must expand.
                new_shelf_length = max(shelf.length, orientation.length)

                # New layer length after placing this carton.
                new_layer_length = max(
                    current_length,
                    shelf.x + new_shelf_length
                )

                # New layer width after placing this carton.
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

                # Score lower is better.
                # First priority: smallest layer area.
                # Second priority: smaller length.
                # Third priority: smaller width.
                score = (
                    new_layer_length * new_layer_width,
                    new_layer_length,
                    new_layer_width,
                )

                if best_score is None or score < best_score:
                    best_score = score
                    best_position = position

        # ------------------------------------------------------------
        # Option 2: Try starting a new shelf
        # ------------------------------------------------------------

        # New shelf starts at the current layer length.
        # This increases the layer length, but may save width.
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
        """
        Actually places a carton into this layer.

        This updates the shelf/layer state and records the placement.
        """

        # If this position starts a new shelf, create that shelf.
        if position.is_new_shelf:
            self.shelves.append(
                Shelf(
                    x=position.x,
                    used_width=orientation.width,
                    length=orientation.length,
                )
            )

        # Otherwise, update an existing shelf.
        else:
            shelf = self.shelves[position.shelf_index]
            shelf.used_width += orientation.width
            shelf.length = max(shelf.length, orientation.length)

        # Record the carton placement.
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
    """
    Represents the final skid plan.

    This contains:
        skid_length
        skid_width
        skid_height
        all layers
        all placements
    """

    skid_length: float
    skid_width: float
    skid_height: float
    layers: list[Layer]

    @property
    def area(self):
        """
        Skid footprint area.

        This is usually what we want to minimize
        when the skid cannot be too large.
        """
        return self.skid_length * self.skid_width

    @property
    def volume(self):
        """
        Total loaded volume of the skid.
        """
        return self.skid_length * self.skid_width * self.skid_height

    @property
    def placements(self):
        """
        Combines placements from all layers into one list.
        """
        result = []

        for layer in self.layers:
            result.extend(layer.placements)

        return result


# ============================================================
# CSV HELPER FUNCTIONS
# ============================================================

def find_column(fieldnames, options):
    """
    Finds the actual CSV column name from a list of acceptable names.

    Example:
        fieldnames = ["Item", "Length", "Width", "Height"]
        options = ["carton_length", "length", "l"]

    This function would return "Length".
    """

    # Normalize column names to lowercase so matching is not case-sensitive.
    normalized = {name.strip().lower(): name for name in fieldnames}

    for option in options:
        if option.lower() in normalized:
            return normalized[option.lower()]

    return None


def parse_float(value, row_number, column_name):
    """
    Converts a CSV value into a float.

    Also checks for:
        - missing value
        - invalid number
        - zero or negative dimensions
    """

    if value is None or str(value).strip() == "":
        raise ValueError(f"Row {row_number}: missing value in '{column_name}'")

    try:
        number = float(value)
    except ValueError:
        raise ValueError(
            f"Row {row_number}: invalid number '{value}' in '{column_name}'"
        )

    if number <= 0:
        raise ValueError(
            f"Row {row_number}: '{column_name}' must be greater than 0"
        )

    return number


def parse_quantity(value, row_number, column_name):
    """
    Converts a CSV quantity value into an integer.

    If quantity is blank, it defaults to 1.
    """

    if value is None or str(value).strip() == "":
        return 1

    try:
        quantity = int(float(value))
    except ValueError:
        raise ValueError(
            f"Row {row_number}: invalid quantity '{value}' in '{column_name}'"
        )

    if quantity < 1:
        raise ValueError(f"Row {row_number}: quantity must be at least 1")

    return quantity


# ============================================================
# SKID MEASUREMENT HELPERS
# ============================================================

def current_skid_length(layers):
    """
    Returns the largest length used by any layer.
    """

    if not layers:
        return 0

    return max(layer.length for layer in layers)


def current_skid_width(layers):
    """
    Returns the largest width used by any layer.
    """

    if not layers:
        return 0

    return max(layer.width_used for layer in layers)


def current_skid_height(layers):
    """
    Returns total stacked height.

    Since layers stack vertically, total height is the sum
    of all layer heights.
    """

    return sum(layer.height for layer in layers)


# ============================================================
# PACKING ALGORITHM
# ============================================================

def pack_items_for_width(items, width_limit):
    """
    Attempts to pack all cartons while respecting a specific width limit.

    Why width_limit?
        The final max width is 90.5 inches, but sometimes a smaller
        width creates a better overall footprint.

    This function tries to pack everything using this one width limit.

    If successful:
        returns a SkidPlan

    If impossible:
        returns None
    """

    layers = []

    # Pack bigger cartons first.
    # This is a common heuristic because large cartons are harder to place later.
    sorted_items = sorted(
        items,
        key=lambda item: (
            item.carton.volume,
            max(item.carton.length, item.carton.width, item.carton.height),
        ),
        reverse=True,
    )

    # Try placing each carton one at a time.
    for item in sorted_items:
        best_choice = None
        best_score = None

        # Current skid dimensions before placing this carton.
        current_length = current_skid_length(layers)
        current_width = current_skid_width(layers)
        current_height = current_skid_height(layers)

        # Try every orientation of the carton.
        for orientation in item.carton.orientations():

            # Reject orientation if it is too wide for this width limit.
            if orientation.width > width_limit + EPS:
                continue

            # Reject orientation if the carton is taller than the entire max height.
            if orientation.height > MAX_LOADED_HEIGHT + EPS:
                continue

            # --------------------------------------------------------
            # Option 1: Try putting carton into an existing layer
            # --------------------------------------------------------
            for layer_index, layer in enumerate(layers):

                position = layer.find_best_position(orientation, width_limit)

                if position is None:
                    continue

                # Calculate what the overall skid dimensions would become.
                new_length = max(current_length, position.new_layer_length)
                new_width = max(current_width, position.new_layer_width)
                new_height = current_height

                # Score lower is better.
                # First priority: smallest footprint area.
                # Second priority: lower height.
                # Then smaller length/width.
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
            # Option 2: Try creating a brand-new layer
            # --------------------------------------------------------

            # A new layer would add this orientation's height.
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

        # If no orientation and no position works, packing failed.
        if best_choice is None:
            return None

        choice_type, layer_index, orientation, position = best_choice

        # Actually place the carton using the best option found.
        if choice_type == "existing_layer":
            layer = layers[layer_index]

            layer.place(
                item=item,
                orientation=orientation,
                position=position,
                layer_number=layer_index + 1,
            )

        else:
            # Create a new layer at the current top height.
            z = current_skid_height(layers)

            new_layer = Layer(
                z=z,
                height=orientation.height,
            )

            # First carton in a new layer starts at x=0, y=0.
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

    # Calculate final skid size after all cartons are packed.
    skid_length = current_skid_length(layers)
    skid_width = current_skid_width(layers)
    skid_height = current_skid_height(layers)

    # Final safety checks.
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
    """
    Creates a list of width limits to test.

    Example:
        If possible widths are from 30 to 90.5,
        and WIDTH_STEP is 0.5,
        this will test:
            30.0, 30.5, 31.0, ..., 90.5

    It also includes exact carton orientation widths,
    because sometimes the best answer happens at an exact carton width.
    """

    lower_bound = 0

    # First, find the minimum width required so every carton
    # can fit in at least one orientation.
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

    # Always test the maximum allowed width.
    widths.add(round(MAX_SKID_WIDTH, 4))

    # Add stepped widths.
    start = math.ceil(lower_bound / WIDTH_STEP) * WIDTH_STEP
    width = start

    while width <= MAX_SKID_WIDTH + EPS:
        widths.add(round(width, 4))
        width += WIDTH_STEP

    # Add exact orientation widths too.
    for item in items:
        for orientation in item.carton.orientations():
            if orientation.width <= MAX_SKID_WIDTH + EPS:
                widths.add(round(orientation.width, 4))

    return sorted(widths)


def optimize_one_skid_for_all_items(items):
    """
    Finds the best skid plan for all cartons together.

    It tries multiple possible width limits.
    For each width limit, it attempts to pack all items.

    Then it chooses the best successful plan.

    Best means:
        1. Smallest skid footprint area
        2. Smallest volume
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


# ============================================================
# CSV PROCESSING
# ============================================================

def add_output_column(fieldnames, column_name):
    """
    Adds a column to the CSV output if it does not already exist.
    """

    if column_name not in fieldnames:
        fieldnames.append(column_name)


def process_csv(input_path, output_path):
    """
    Main CSV workflow.

    Steps:
        1. Read input CSV
        2. Find dimension columns
        3. Convert rows into Carton/Item objects
        4. Optimize one skid for all cartons
        5. Write original rows back with skid columns added
    """

    # ------------------------------------------------------------
    # Step 1: Read the CSV
    # ------------------------------------------------------------
    with open(input_path, "r", newline="", encoding="utf-8-sig") as infile:
        reader = csv.DictReader(infile)

        if not reader.fieldnames:
            raise ValueError("CSV has no headers")

        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # ------------------------------------------------------------
    # Step 2: Find the dimension/quantity columns
    # ------------------------------------------------------------
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

    # ------------------------------------------------------------
    # Step 3: Convert CSV rows into Item objects
    # ------------------------------------------------------------
    items = []

    for index, row in enumerate(rows):
        # CSV row number is index + 2 because:
        #   index starts at 0
        #   CSV row 1 is the header
        csv_row_number = index + 2

        carton = Carton(
            length=parse_float(row[length_col], csv_row_number, length_col),
            width=parse_float(row[width_col], csv_row_number, width_col),
            height=parse_float(row[height_col], csv_row_number, height_col),
        )

        # If the CSV has a quantity column, use it.
        # Otherwise each row counts as one carton.
        quantity = (
            parse_quantity(row[quantity_col], csv_row_number, quantity_col)
            if quantity_col
            else 1
        )

        # Create one Item object for each physical carton.
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

    # ------------------------------------------------------------
    # Step 4: Optimize one skid for all cartons
    # ------------------------------------------------------------
    plan = optimize_one_skid_for_all_items(items)

    # ------------------------------------------------------------
    # Step 5: Prepare output columns
    # ------------------------------------------------------------
    add_output_column(fieldnames, "skid_length")
    add_output_column(fieldnames, "skid_width")
    add_output_column(fieldnames, "skid_height")

    # ------------------------------------------------------------
    # Step 6: Store skid results
    # ------------------------------------------------------------
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

        # This prints how each carton was placed.
        # The CSV only gets skid dimensions for now.
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

    # Since this is one combined skid, every row gets the same skid size.
    for row in rows:
        row["skid_length"] = skid_length
        row["skid_width"] = skid_width
        row["skid_height"] = skid_height

    # ------------------------------------------------------------
    # Step 7: Write output CSV
    # ------------------------------------------------------------
    with open(output_path, "w", newline="", encoding="utf-8") as outfile:
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Wrote output to: {output_path}")


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

def main():
    """
    This lets the script run from the terminal like:

        python skid_optimizer_all_commented.py input.csv output.csv
    """

    if len(sys.argv) != 3:
        print("Usage:")
        print("python skid_optimizer_all_commented.py input.csv output.csv")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    process_csv(input_path, output_path)


if __name__ == "__main__":
    main()