import argparse

from excel_utils_practical_v10 import process_excel_to_csv
from optimizer_practical_v10 import print_elapsed


def main():
    parser = argparse.ArgumentParser(
        description="Optimize skid dimensions for each merged-cell group in an Excel sheet."
    )

    parser.add_argument(
        "input_excel",
        help="Path to the input .xlsx file",
    )

    parser.add_argument(
        "output_csv",
        help="Path to the output .csv file",
    )

    parser.add_argument(
        "--sheet",
        default=None,
        help="Optional sheet name. If omitted, the active sheet is used.",
    )

    args = parser.parse_args()

    process_excel_to_csv(
        input_path=args.input_excel,
        output_path=args.output_csv,
        sheet_name=args.sheet,
    )

    print_elapsed("Program finished.")


if __name__ == "__main__":
    main()
