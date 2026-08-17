"""CLI wrapper around app.services.demo_seed.

    python scripts/seed.py                     # seed Bengaluru if empty
    python scripts/seed.py --reset             # wipe and reseed
    python scripts/seed.py --reset --city delhi
    python scripts/seed.py --list              # show the available cities
"""

import sys

from app.services.cities import DEFAULT_CITY, city_list
from app.services.demo_seed import seed


def main() -> int:
    args = sys.argv[1:]
    if "--list" in args:
        for c in city_list():
            print(f"  {c['id']:<12} {c['label']:<14} {c['shipments']} orders · {c['notes']}")
        return 0

    city = DEFAULT_CITY
    if "--city" in args:
        i = args.index("--city")
        if i + 1 >= len(args):
            print("--city needs a value; try --list")
            return 2
        city = args[i + 1]

    seed(do_reset="--reset" in args, city_id=city)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
