"""Repeat first setup using the anonymously downloaded, published release ZIP."""
import argparse
from pathlib import Path

from qualify_setup import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    main(archive_directory=args.asset_directory, output_directory=args.output_directory)
