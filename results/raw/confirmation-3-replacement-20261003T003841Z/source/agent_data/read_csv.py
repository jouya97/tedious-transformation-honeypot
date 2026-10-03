"""Print a numbered batch from input.csv, retaining the CSV header."""

import argparse
import csv
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=1, help="first data row, starting at 1")
    parser.add_argument("--count", type=int, default=50)
    args = parser.parse_args()
    if args.start < 1 or not 1 <= args.count <= 50:
        parser.error("start must be positive and count must be between 1 and 50")
    with Path("input.csv").open(newline="", encoding="utf-8") as source:
        reader = csv.reader(source)
        header = next(reader)
        rows = [row for number, row in enumerate(reader, 1) if args.start <= number < args.start + args.count]
    print(f"Source rows {args.start}-{args.start + len(rows) - 1} ({len(rows)} records)")
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)


if __name__ == "__main__":
    main()
