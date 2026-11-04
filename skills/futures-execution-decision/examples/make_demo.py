"""Create a synthetic 20-day minute grid and a matching request in a NEW directory."""
import argparse
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="new directory for request.json and synthetic.npz")
    dest = parser.parse_args().directory
    if dest.exists():
        parser.error(f"{dest} already exists; select a new directory")

    dates = [str(day).replace("-", "") for day in np.busday_offset("2026-07-06", np.arange(20))]
    start = datetime(2026, 8, 3, 9, 15)
    labels = [(start + timedelta(minutes=i)).strftime("%H:%M") for i in range(61)]
    shape = (len(dates), len(labels))
    dest.mkdir(parents=True)
    np.savez_compressed(
        dest / "synthetic.npz", dates=dates, labels=labels,
        close=np.full(shape, 100.0), volume=np.full(shape, 100), mask=np.ones(shape, dtype=bool),
    )
    shutil.copyfile(Path(__file__).with_name("request.json"), dest / "request.json")
    print(f"Synthetic example ready: {dest / 'request.json'}")


if __name__ == "__main__":
    main()
