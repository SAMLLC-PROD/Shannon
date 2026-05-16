import argparse
from shannon.openclaw import regenerate_context

def main():
    parser = argparse.ArgumentParser(description="Shannon CLI commands")
    subparsers = parser.add_subparsers(dest='command')

    # Regenerate context command
    regen_parser = subparsers.add_parser('regenerate', help='Regenerate Shannon context')
    regen_parser.set_defaults(func=regenerate_context_command)

    args = parser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        parser.print_help()

def regenerate_context_command(args):
    try:
        result = regenerate_context()
        print(f"Status: {result['status']}")
        print(f"Entries Processed: {result['entries_processed']}")
        print(f"Output File: {result['output_file']}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
