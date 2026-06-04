from __future__ import annotations

import csv

from models import Carton, Item
from optimizer import optimize_one_skid_for_all_items
from visualizer_plotly import export_plan_to_plotly_html


# ============================================================
# ACCEPTED CSV COLUMN NAMES
# ============================================================

LENGTH_COL_OPTIONS = [
    "carton_length",
    "carton length",
    "length",
    "l",
]

WIDTH_COL_OPTIONS = [
    "carton_width",
    "carton width",
    "width",
    "w",
]

HEIGHT_COL_OPTIONS = [
    "carton_height",
    "carton height",
    "height",
    "h",
]

QUANTITY_COL_OPTIONS = [
    "quantity",
    "qty",
    "count",
]


# ============================================================
# CSV HELPERS
# ============================================================

def normalize_column_name(name: str) -> str:
    """
    Normalizes column names so these all match:

    Carton Length
    carton_length
    carton-length
    cartonlength
    """

    return (
        name.strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )


def find_column(fieldnames: list[str], options: list[str]) -> str | None:
    """
    Finds the real CSV column name from a list of acceptable names.
    """

    normalized_fieldnames = {
        normalize_column_name(name): name
        for name in fieldnames
    }

    for option in options:
        normalized_option = normalize_column_name(option)

        if normalized_option in normalized_fieldnames:
            return normalized_fieldnames[normalized_option]

    return None


def parse_float(value, row_number: int, column_name: str) -> float:
    """
    Converts a CSV value into a float.

    Raises a clear error if the cell is blank or invalid.
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


def parse_quantity(value, row_number: int, column_name: str) -> int:
    """
    Converts quantity into an integer.

    Blank quantity defaults to 1.
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


def add_output_column(fieldnames: list[str], column_name: str) -> None:
    """
    Adds an output column only if it does not already exist.
    """

    if column_name not in fieldnames:
        fieldnames.append(column_name)


# ============================================================
# MAIN CSV WORKFLOW
# ============================================================

def process_csv(input_path: str, output_path: str) -> None:
    """
    Reads the input CSV, calculates one skid size for all cartons,
    and writes a new CSV with skid_length, skid_width, and skid_height.
    """

    # ------------------------------------------------------------
    # Step 1: Read CSV
    # ------------------------------------------------------------
    with open(input_path, "r", newline="", encoding="utf-8-sig") as infile:
        reader = csv.DictReader(infile)

        if not reader.fieldnames:
            raise ValueError("CSV has no headers")

        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # ------------------------------------------------------------
    # Step 2: Find required columns
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
    # Step 3: Convert rows into Item objects
    # ------------------------------------------------------------
    items: list[Item] = []

    for index, row in enumerate(rows):
        # +2 because:
        # index starts at 0
        # row 1 is the CSV header
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

    # ------------------------------------------------------------
    # Step 4: Optimize one skid for all cartons
    # ------------------------------------------------------------
    plan = optimize_one_skid_for_all_items(items)

    # ------------------------------------------------------------
    # Step 5: Add output columns
    # ------------------------------------------------------------
    add_output_column(fieldnames, "skid_length")
    add_output_column(fieldnames, "skid_width")
    add_output_column(fieldnames, "skid_height")

    # ------------------------------------------------------------
    # Step 6: Save results
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

        export_plan_to_plotly_html(plan, "skid_visual.html")

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
                f"x={round(placement.x, 2)}, "
                f"y={round(placement.y, 2)}, "
                f"z={round(placement.z, 2)}, "
                f"placed as "
                f"{round(placement.length, 2)} x "
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