#!/usr/bin/env python3
import sys
import os
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger('erp_export_job')


def main():
    parser = argparse.ArgumentParser(description='ERP Export Bot CLI')
    parser.add_argument('--export', type=str, help='Name of the export flow to run')
    parser.add_argument('--all', action='store_true', help='Run all export flows')
    parser.add_argument('--list', action='store_true', help='List available export flows')
    args = parser.parse_args()

    from app import app

    with app.app_context():
        from app import db
        db.create_all()

        from services.erp_export_flows import list_flows, EXPORT_FLOWS

        if args.list:
            flows = list_flows()
            print("\nAvailable export flows:")
            for f in flows:
                print(f"  {f['name']:20s} — {f['label']} ({f['description']})")
            sys.exit(0)

        if not args.export and not args.all:
            parser.print_help()
            print("\nError: specify --export <name> or --all")
            sys.exit(1)

        from services.erp_export_bot import run_export_sync, check_concurrent_run

        exports_to_run = []
        if args.all:
            exports_to_run = list(EXPORT_FLOWS.keys())
        else:
            exports_to_run = [args.export]

        success_count = 0
        fail_count = 0

        for export_name in exports_to_run:
            print(f"\n{'='*60}")
            print(f"Running export: {export_name}")
            print(f"{'='*60}")

            if check_concurrent_run(export_name):
                print(f"SKIPPED: {export_name} is already running")
                continue

            try:
                result = run_export_sync(export_name, triggered_by='scheduled')
                status = result.get('status', 'unknown')
                print(f"Result: {status}")

                if result.get('file_name'):
                    print(f"File: {result['file_name']} ({result.get('file_size', 0):,} bytes)")

                if result.get('error_message'):
                    print(f"Error: {result['error_message']}")

                if status in ('success', 'partial'):
                    success_count += 1
                else:
                    fail_count += 1

            except Exception as e:
                print(f"FAILED: {e}")
                fail_count += 1

        print(f"\n{'='*60}")
        print(f"Summary: {success_count} succeeded, {fail_count} failed")
        print(f"{'='*60}")

        sys.exit(1 if fail_count > 0 else 0)


if __name__ == '__main__':
    main()
