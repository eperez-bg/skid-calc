import sys

from csv_utils import process_csv


def main():
    """
    Run the script from terminal like:

    python3 main.py input.csv output.csv
    """

    if len(sys.argv) != 3:
        print("Usage:")
        print("python3 main.py input.csv output.csv")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    process_csv(input_path, output_path)


if __name__ == "__main__":
    main()