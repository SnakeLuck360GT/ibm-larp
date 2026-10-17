"""Create, view and summarise the damage-cost mock dataset (from pipeline/04_policy.py).

Interactive menu:
    python pipeline/damage_data.py

Or directly:
    python pipeline/damage_data.py create
    python pipeline/damage_data.py view haze --state Sarawak --rows 30
    python pipeline/damage_data.py summary            # all datasets
    python pipeline/damage_data.py summary costs
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("policy", ROOT / "pipeline" / "04_policy.py")
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)

DATASETS = {  # short name -> file stem
    "states": "state_reference",
    "haze": "haze_daily",
    "costs": "damage_costs_daily",
}
pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)


def path(name: str) -> Path:
    return policy.MOCK_DIR / f"{DATASETS[name]}.csv"


def create(force: bool = False) -> None:
    if all(path(n).exists() for n in DATASETS) and not force:
        print(f"Already exists in {policy.MOCK_DIR.relative_to(ROOT)} (use --force / answer 'y' to regenerate).")
        return
    policy.MOCK_DIR.mkdir(parents=True, exist_ok=True)
    for stem, df in policy.build_mock().items():
        df.to_csv(policy.MOCK_DIR / f"{stem}.csv", index=False)
        print(f"  wrote {stem}.csv  ({len(df):,} rows x {df.shape[1]} cols)")
    print(f"Done -> {policy.MOCK_DIR.relative_to(ROOT)}")


def load(name: str) -> pd.DataFrame:
    if not path(name).exists():
        print("Dataset not found — creating it first.")
        create()
    return pd.read_csv(path(name))


def view(name: str, state: str | None = None, rows: int = 20) -> None:
    df = load(name)
    if state:
        df = df[df.state.str.lower() == state.lower()]
        if df.empty:
            print(f"No rows for '{state}'. States: {', '.join(policy.STATE_NAMES)}")
            return
    print(f"\n=== {DATASETS[name]}  ({len(df):,} rows{' for ' + state if state else ''})")
    print(df.head(rows).to_string(index=False))
    if len(df) > rows:
        print(f"... {len(df) - rows:,} more rows (use --rows N)")


def summary(name: str | None = None) -> None:
    for n in ([name] if name else DATASETS):
        df = load(n)
        print(f"\n=== {DATASETS[n]}  {df.shape[0]:,} rows x {df.shape[1]} cols")
        if "date" in df:
            print(f"Dates: {df.date.min()} -> {df.date.max()}  |  states: {df.state.nunique()}")
        missing = df.isna().sum()
        print("Missing values:", "none" if missing.sum() == 0 else missing[missing > 0].to_dict())
        print(df.describe().T[["mean", "min", "max"]].round(1).to_string())

        if n == "haze":
            print("\nPeak API and days per band, by state:")
            bands = pd.crosstab(df.state, df.api_band)
            bands.insert(0, "peak_api", df.groupby("state").api_max.max().round())
            bands["school_closed_days"] = df.groupby("state").school_closed.sum()
            print(bands.sort_values("peak_api", ascending=False).to_string())
        if n == "costs":
            cats = [c for c in policy.CATEGORIES if c in df]
            print("\nTotal by category (RM m):")
            print((df[cats].sum() / 1e6).sort_values(ascending=False).round(1).to_string())
            print("\nTotal by state (RM m):")
            print((df.groupby("state").total_damage_rm.sum() / 1e6).sort_values(ascending=False).round(1).to_string())
            print(f"\nNational total: RM {df.total_damage_rm.sum() / 1e9:.2f} bn  (mock 'ground truth' — DUMMY)")


def menu() -> None:
    names = list(DATASETS)
    while True:
        print("\n--- Damage-cost mock data ---")
        print(" 1) Create dataset\n 2) View a dataset\n 3) Summary\n 4) Quit")
        choice = input("> ").strip()
        if choice == "1":
            exists = all(path(n).exists() for n in DATASETS)
            create(force=exists and input("Overwrite existing files? [y/N] ").strip().lower() == "y")
        elif choice in ("2", "3"):
            print("Datasets: " + ", ".join(f"{i}) {n}" for i, n in enumerate(names, 1))
                  + (", 4) all" if choice == "3" else ""))
            pick = input("> ").strip()
            if choice == "3" and pick == "4":
                summary()
                continue
            if not pick.isdigit() or not 1 <= int(pick) <= len(names):
                print("Pick a number from the list.")
                continue
            name = names[int(pick) - 1]
            if choice == "2":
                state = input("Filter by state (blank for all): ").strip() or None
                rows = input("Rows to show [20]: ").strip()
                view(name, state, int(rows) if rows.isdigit() else 20)
            else:
                summary(name)
        elif choice in ("4", "q", "quit", "exit"):
            return
        else:
            print("Choose 1–4.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Create, view and summarise the damage-cost mock dataset.")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("create", help="generate the CSVs into data/mock/damage/")
    c.add_argument("--force", action="store_true", help="overwrite existing files")
    v = sub.add_parser("view", help="print rows of a dataset")
    v.add_argument("dataset", choices=DATASETS)
    v.add_argument("--state")
    v.add_argument("--rows", type=int, default=20)
    s = sub.add_parser("summary", help="summary statistics")
    s.add_argument("dataset", nargs="?", choices=DATASETS)
    a = ap.parse_args(argv)

    if a.cmd == "create":
        create(a.force)
    elif a.cmd == "view":
        view(a.dataset, a.state, a.rows)
    elif a.cmd == "summary":
        summary(a.dataset)
    else:
        menu()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)
