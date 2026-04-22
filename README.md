# auto-invoice

Invoice annoying. Simple invoice generation from a LaTeX template.

## Setup

1. Fill in the required values in [`.env`](/Users/FranklinZhao/projects/auto-invoice/.env).
2. Make sure `task` is installed.
3. Install a TeX toolchain if you want LaTeX-based PDF generation.
   MacTeX is the intended path, and the script prefers `latexmk` and `pdflatex`.

## Usage

Run:

```bash
task invoice
```

The script will prompt for:

- invoice number
- start date
- end date
- hourly rate
- hours worked

## Output

- Each run writes to `invoices/invoice-<number>/`.
- The invoice PDF is named `invoice-<number>.pdf`.
- The rendered email is written as `email.txt`.
- If you run with `--skip-pdf`, the rendered LaTeX template is written as `invoice-<number>.tex`.

Example:

```bash
task invoice -- --skip-pdf
```
