"""CLI entry point for the catalogue transformer.

Usage:
    python scripts/transform_cli.py file1.xlsx file2.xlsx -o output.xlsx
"""

import argparse
import sys
from pathlib import Path

# Add src/ to path so transformer and its siblings can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from transformer import transform_files  # noqa: E402


def _print_summary(summary: dict) -> None:
    total_in = sum(v['total_rows'] for v in summary['files'].values())

    print()
    print('── Summary ────────────────────────────────────────')
    print(f'  Source rows read : {total_in}')
    if len(summary['files']) > 1:
        for fname, fdata in summary['files'].items():
            print(f'    {fname}: {fdata["total_rows"]}')

    print(f'  Rows mapped      : {summary["total_mapped"]}')
    for sheet, count in summary['mapped_by_sheet'].items():
        if count:
            print(f'    {sheet}: {count}')

    print(f'  Rows unmapped    : {summary["total_unmapped"]}')
    for reason, count in summary['unmapped_by_reason'].items():
        print(f'    {reason}: {count}')

    print(
        f'  Missing dims     : {summary["missing_dimensions"]} '
        '(amber-highlighted in output — expected)'
    )
    print('───────────────────────────────────────────────────')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Transform brand master catalogue files to eProcess / M10 import format.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'inputs',
        nargs='+',
        metavar='FILE',
        help='One or more source catalogue .xlsx files',
    )
    parser.add_argument(
        '-o', '--output',
        default='eprocess_import.xlsx',
        metavar='OUTPUT',
        help='Output file path (default: eprocess_import.xlsx)',
    )
    args = parser.parse_args()

    input_paths = [Path(p) for p in args.inputs]
    missing = [str(p) for p in input_paths if not p.exists()]
    if missing:
        for m in missing:
            print(f'Error: file not found: {m}', file=sys.stderr)
        sys.exit(1)

    print(f'Transforming {len(input_paths)} file(s)...')
    try:
        summary = transform_files(input_paths, args.output)
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        sys.exit(1)

    _print_summary(summary)
    print(f'\nOutput: {Path(args.output).resolve()}')


if __name__ == '__main__':
    main()
