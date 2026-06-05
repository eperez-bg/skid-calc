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


# FIND_COLUMN:
#   $ fieldnames : List of Strings that represents the column headers in the CSV
#   $ options : List of Strings that represents valid options for a single header
# SUMMARY:
#   - Creates an object where { normalized_column_header : un-normalized_column_header }, then goes through each string in valid options, normalizes it and if it matches any normalized column header, returns the raw column header string
# EXAMPLE:
#   - When opening the CSV and trying to find which name is being used for each column, call find_column using list of CSV file headers and each list of valid names for each valid column name (find_column(HEADERS, WIDTH_OPTIONS), find_column(HEADERS, HEIGHT_OPTIONS))
def find_column(fieldnames: list[str], options: list[str]) -> str | None:
    """
    Finds the real CSV column name from a list of acceptable names.
    """

    # (1) For each column title, normalize and set the Un-normalized version as value to Normalized key for each column header
    normalized_fieldnames = {
        normalize_column_name(name): name
        for name in fieldnames
    }

    # (2) For each option in options list (used for valid column names), normalize it, and if normalized version in list of valid options, return 'real' column name from object of normalized field names 
    for option in options:
        normalized_option = normalize_column_name(option)

        if normalized_option in normalized_fieldnames:
            return normalized_fieldnames[normalized_option]

    # If normalized column name not in list of valid options, return None/Null, i.e., the column name is not valid
    return None


# PARSE_FLOAT:
#   $ value : cell value from CSV file
#   $ row_number : int representation of which row $value is in
#   $ column_name : str representation of which column the $value is in
# SUMMARY:
#   - Ensure value is a valid number that is greater than 0, then parses it into a float and returns the float
# EXAMPLE:
#   - When taking each row in the CSV and coverting them into Items, use parse_float() to turn each value into a usable float that Python can use
def parse_float(value, row_number: int, column_name: str) -> float:
    """
    Converts a CSV value into a float.

    Raises a clear error if the cell is blank or invalid.
    """

    # (1) Check that value is not None/Null or empty string, if so, return error
    if value is None or str(value).strip() == "":
        raise ValueError(f"Row {row_number}: missing value in '{column_name}'")

    # (2) Try parsing value into float, catch invalid value error
    try:
        number = float(value)
    except ValueError:
        raise ValueError(
            f"Row {row_number}: invalid number '{value}' in '{column_name}'"
        )

    # (3) Check that parsed float is not <= 0, if so, throw Value Error
    if number <= 0:
        raise ValueError(
            f"Row {row_number}: '{column_name}' must be greater than 0"
        )

    return number


# PARSE_QUANTITY:
#   $ value : cell value from CSV file
#   $ row_number : int representation of row number where $value is
#   $ column_name: str representation of column name where $value is
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


# ADD_OUTPUT_COLUMN:
#   $ fieldnames : list of string representations of column headers
#   $ column_name : string representation of new column header to add
# SUMMARY:
#   - Checks if $column_name is not already in list of column headers, if not, adds it to list of column headers
# EXAMPLE:
#   - After calculating best skid dimensions, use add_output_column() to add skid length, width, and height to list of headers
def add_output_column(fieldnames: list[str], column_name: str) -> None:
    """
    Adds an output column only if it does not already exist.
    """

    if column_name not in fieldnames:
        fieldnames.append(column_name)


# ============================================================
# MAIN CSV WORKFLOW
# ============================================================

# PROCESS_CSV:
#   $ input_path : String representation of path to CSV input file (where Carton dimensions are read from)
#   $ output_path : String representation of path to CSV output file (where Skid dimensions will be written to)
# SUMMARY:
#   - (1) Opens CSV from $input_path and ensures that the first row contains headers, if not throws error. Sets fieldnames = column headers, rows = list of each row in CSV
#   - (2) Use find_column() to find which valid header name is being used for each Width, Length, Height. Sets missing = [], and if length, width, or height are missing from columns, throws error (qty can be missing)
#   - (3) Iterate over enumarate of rows, (e.g,. (2, row[1]) --> (3, row[2]) --> etc.), creates a Carton object by parsing each value in row to floats, then iterates over however many copy_number's you need of the carton, and creates
#         an Item object for each one using row number, copy number, and Carton object, then appends each Item obj to items list.
#   - (4) Use function optimize_one_skid_for_all_items() from "optimizer.py" file that returns a SkidPlan object and sets it to plan variable
#   - (5) Add skid width, length, and height headers to headers list using add_output_column() function
#   - (6) Ensure function returned a complete SkidPlan, if not, inform user that no valid skid found for all cartons. Use export_plan_to_plotly_html() function from "visualizer_plotly.py" file that creates an HTML file to view a 3D version
#         of the skid. Extract the skid dimensions and placements of each carton, then print them to the console
#   - (7) Write the skid dimensions to the output CSV file
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