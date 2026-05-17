import argparse
from shannon.openclaw import generate_context_file

def regenerate_context():
    """
    Regenerate the Shannon context file using the openclaw module.
    """
    path = generate_context_file(days_back=1)
    print(f"Context regeneration complete. Output file: {path}")

def main():
    parser = argparse.ArgumentParser(description="Shannon CLI")
    subparsers = parser.add_subparsers(dest='command')

    # Add regenerate command
    parser_regenerate = subparsers.add_parser('regenerate', help='Regenerate Shannon context')
    parser_regenerate.set_defaults(func=regenerate_context)

    args = parser.parse_args()
    if args.command:
        args.func()

if __name__ == "__main__":
    main()
