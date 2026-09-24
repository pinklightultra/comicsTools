"""
Minimal end-to-end example.

    python examples/example_usage.py pencils.pdf inked_page.png
"""
import sys

import pencil_cleaner as pc


def main():
    if len(sys.argv) != 3:
        print("usage: example_usage.py <pencils.pdf> <inked_page.png>")
        raise SystemExit(1)

    pencils_pdf, inked_page = sys.argv[1], sys.argv[2]

    # Step 1: pencils-only PDF -> faint, printable PDF to ink over.
    pc.prep_pdf_for_printing(pencils_pdf, "print_me.pdf", opacity="light")
    print("wrote print_me.pdf")

    # Step 2: after inking + rescanning, strip what's left of the pencil.
    pc.remove_pencils(inked_page, "clean_page.png", svg_output_path="ink.svg")
    print("wrote clean_page.png and ink.svg")


if __name__ == "__main__":
    main()
