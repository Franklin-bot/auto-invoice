from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TEMPLATE_PATH = ROOT / "invoice-template.tex"
EMAIL_TEMPLATE_PATH = ROOT / "email_template.yml"
OUTPUT_DIR = ROOT / "invoices"
MAC_TEX_BIN_DIRS = (
    Path("/Library/TeX/texbin"),
    Path("/usr/texbin"),
)
REQUIRED_ENV_VARS = (
    "EMPLOYEE_NAME",
    "EMPLOYEE_ADDRESS_LINE_1",
    "EMPLOYEE_ADDRESS_LINE_2",
    "EMPLOYEE_PERSONAL_EMAIL",
    "EMPLOYEE_EMAIL",
    "EMPLOYEE_PHONE",
    "EMPLOYER_NAME",
    "EMPLOYER_ADDRESS_LINE_1",
    "EMPLOYER_ADDRESS_LINE_2",
    "EMPLOYER_EMAIL",
    "TEAM_NAME",
    "MANAGER_NAME",
    "SERVICE_DESCRIPTION",
)
FILENAME_SAFE_CHARS = re.compile(r"[^a-z0-9]+")


def parse_args() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(
        description="Render the invoice template and generate a PDF."
    )
    parser.add_argument(
        "--invoice-date",
        default=f"{today:%Y, %b} {today.day}",
        help="Invoice date string shown in the template.",
    )
    parser.add_argument(
        "--invoice-period",
        default="",
        help="Billing period string shown in the template.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional explicit PDF output path. Defaults to invoices/invoice-<number>.pdf.",
    )
    parser.add_argument(
        "--keep-tex",
        action="store_true",
        help="Keep the rendered .tex file next to the generated PDF.",
    )
    parser.add_argument(
        "--skip-pdf",
        action="store_true",
        help="Render the .tex file without compiling the PDF.",
    )
    return parser.parse_args()


def prompt_decimal(prompt_text: str) -> Decimal:
    while True:
        raw_value = input(prompt_text).strip()
        try:
            value = Decimal(raw_value)
        except Exception:
            print("Please enter a valid number.")
            continue

        if value < 0:
            print("Please enter a non-negative number.")
            continue

        return value


def prompt_text(prompt_text: str) -> str:
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        print("Please enter a value.")


def load_env_file(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        raise FileNotFoundError(f"Missing environment file: {env_path}")

    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line: {raw_line!r}")
        key, value = line.split("=", 1)
        cleaned_value = value.strip()
        if (
            len(cleaned_value) >= 2
            and cleaned_value[0] == cleaned_value[-1]
            and cleaned_value[0] in {"'", '"'}
        ):
            cleaned_value = cleaned_value[1:-1]
        values[key.strip()] = cleaned_value
    return values


def latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


def normalize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    normalized = normalize_money(value)
    return f"{normalized:,.2f}"


def format_hours(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{normalized:.2f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def render_template(template: str, values: dict[str, str]) -> str:
    missing = sorted(set(re.findall(r"\[\[([A-Z0-9_]+)\]\]", template)) - values.keys())
    if missing:
        missing_names = ", ".join(missing)
        raise KeyError(f"Missing template values: {missing_names}")

    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"[[{key}]]", value)
    return rendered


def load_email_template(template_path: Path) -> dict[str, str]:
    raw_template = template_path.read_text()
    matches = re.findall(r'(\w+):\s*"(.*?)"', raw_template, flags=re.DOTALL)
    if not matches:
        raise ValueError(f"Could not parse email template: {template_path}")

    parsed: dict[str, str] = {}
    for key, value in matches:
        parsed[key] = value.replace("\\n", "\n").strip()
    return parsed


def render_email_template(template: dict[str, str], values: dict[str, str]) -> str:
    subject = template.get("subject", "")
    body = template.get("body", "")
    for key, value in values.items():
        subject = subject.replace(key, value)
        body = body.replace(key, value)
    return f"Subject: {subject}\n\n{body}\n"


def make_output_stem(invoice_number: str) -> str:
    normalized = invoice_number.strip().lower()
    normalized = re.sub(r"^invoice[\s-]*", "", normalized)
    normalized = FILENAME_SAFE_CHARS.sub("-", normalized).strip("-")
    if not normalized:
        normalized = "invoice"
    return f"invoice-{normalized}"


def find_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found is not None:
        return found

    for bin_dir in MAC_TEX_BIN_DIRS:
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)

    return None


def draw_right_aligned_text(
    pdf: canvas.Canvas,
    text: str,
    right_x: float,
    y: float,
    font_name: str,
    font_size: float,
) -> None:
    text_width = stringWidth(text, font_name, font_size)
    pdf.setFont(font_name, font_size)
    pdf.drawString(right_x - text_width, y, text)


def build_fallback_pdf(output_path: Path, invoice_data: dict[str, str]) -> None:
    pdf = canvas.Canvas(str(output_path), pagesize=letter)
    page_width, page_height = letter

    left_margin = 72
    right_margin = page_width - 72
    y = page_height - 72

    pdf.setTitle(f"Invoice {invoice_data['INVOICE_NUMBER']}")

    draw_right_aligned_text(pdf, "INVOICE", right_margin, y, "Helvetica-Bold", 22)
    y -= 28
    draw_right_aligned_text(
        pdf,
        f"Number: {invoice_data['INVOICE_NUMBER']}",
        right_margin,
        y,
        "Helvetica-Bold",
        11,
    )
    y -= 16
    draw_right_aligned_text(
        pdf,
        f"Date: {invoice_data['INVOICE_DATE']}",
        right_margin,
        y,
        "Times-Roman",
        11,
    )
    y -= 16
    draw_right_aligned_text(
        pdf,
        f"Period: {invoice_data['INVOICE_PERIOD']}",
        right_margin,
        y,
        "Times-Roman",
        11,
    )

    y -= 48
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(left_margin, y, "From")
    pdf.drawString(left_margin + 260, y, "Bill To")

    y -= 22
    from_lines = [
        invoice_data["EMPLOYEE_NAME"],
        invoice_data["EMPLOYEE_ADDRESS_LINE_1"],
        invoice_data["EMPLOYEE_ADDRESS_LINE_2"],
        invoice_data["EMPLOYEE_PERSONAL_EMAIL"],
        invoice_data["EMPLOYEE_EMAIL"],
        invoice_data["EMPLOYEE_PHONE"],
    ]
    bill_to_lines = [
        invoice_data["EMPLOYER_NAME"],
        invoice_data["EMPLOYER_ADDRESS_LINE_1"],
        invoice_data["EMPLOYER_ADDRESS_LINE_2"],
        invoice_data["EMPLOYER_EMAIL"],
    ]

    for index in range(max(len(from_lines), len(bill_to_lines))):
        left_text = from_lines[index] if index < len(from_lines) else ""
        right_text = bill_to_lines[index] if index < len(bill_to_lines) else ""
        pdf.setFont("Helvetica-Bold" if index == 0 else "Times-Roman", 11)
        pdf.drawString(left_margin, y, left_text)
        pdf.drawString(left_margin + 260, y, right_text)
        y -= 16

    y -= 24
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(left_margin, y, "Services")
    y -= 24
    pdf.setFont("Times-Roman", 11)
    pdf.drawString(
        left_margin,
        y,
        "Providing consulting services on infrastructure and other software",
    )
    y -= 16
    pdf.drawString(
        left_margin,
        y,
        "work for expert-facing tools and internal teams.",
    )
    y -= 20
    pdf.drawString(left_margin, y, f"Team name: {invoice_data['TEAM_NAME']}")
    y -= 16
    pdf.drawString(left_margin, y, f"Manager: {invoice_data['MANAGER_NAME']}")

    y -= 44
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(left_margin, y, "Description")
    pdf.drawString(left_margin + 300, y, "Summary")
    y -= 18
    pdf.line(left_margin, y, right_margin, y)

    y -= 20
    pdf.setFont("Times-Roman", 11)
    pdf.drawString(left_margin, y, invoice_data["SERVICE_DESCRIPTION"])
    pdf.drawString(left_margin + 300, y, f"Hours: {invoice_data['HOURS_WORKED']}")
    y -= 16
    pdf.drawString(
        left_margin + 300,
        y,
        f"Rate: ${invoice_data['HOURLY_RATE']} (USD)",
    )
    y -= 16
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(
        left_margin + 300,
        y,
        f"Total due: ${invoice_data['TOTAL_DUE']} (USD)",
    )

    y -= 48
    pdf.setFont("Times-Roman", 11)
    pdf.drawString(left_margin, y, "Payment details are setup in Rippling and Ramp.")

    pdf.showPage()
    pdf.save()


def build_pdf(tex_path: Path, output_path: Path, invoice_data: dict[str, str]) -> None:
    latexmk_path = find_executable("latexmk")
    pdflatex_path = find_executable("pdflatex")
    if latexmk_path is None and pdflatex_path is None:
        build_fallback_pdf(output_path, invoice_data)
        return

    with tempfile.TemporaryDirectory(prefix="auto-invoice-") as temp_dir:
        temp_dir_path = Path(temp_dir)
        temp_tex_path = temp_dir_path / tex_path.name
        shutil.copy2(tex_path, temp_tex_path)

        if latexmk_path is not None:
            subprocess.run(
                [
                    latexmk_path,
                    "-pdf",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    f"-output-directory={temp_dir_path}",
                    temp_tex_path.name,
                ],
                check=True,
                cwd=temp_dir_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.run(
                [
                    pdflatex_path,
                    "-interaction=nonstopmode",
                    "-output-directory",
                    str(temp_dir_path),
                    str(temp_tex_path),
                ],
                check=True,
                cwd=temp_dir_path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        compiled_pdf = temp_dir_path / f"{tex_path.stem}.pdf"
        shutil.copy2(compiled_pdf, output_path)


def main() -> None:
    args = parse_args()
    env_values = load_env_file(ENV_PATH)

    missing_env = [name for name in REQUIRED_ENV_VARS if not env_values.get(name)]
    if missing_env:
        missing_names = ", ".join(missing_env)
        raise ValueError(f"Missing required environment variables in .env: {missing_names}")

    invoice_number = prompt_text("Invoice number: ")
    start_date = prompt_text("Start date: ")
    end_date = prompt_text("End date: ")
    hourly_rate = prompt_decimal("Hourly rate (USD): ")
    hours_worked = prompt_decimal("Hours worked: ")
    total_due = hourly_rate * hours_worked
    output_stem = make_output_stem(invoice_number)
    output_dir = OUTPUT_DIR / output_stem
    output_path = args.output or output_dir / f"{output_stem}.pdf"
    tex_output_path = output_path.with_suffix(".tex")
    email_output_path = output_path.with_name("email.txt")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_template = TEMPLATE_PATH.read_text()
    substitutions = {
        key: latex_escape(value) for key, value in env_values.items()
    }
    substitutions.update(
        {
            "INVOICE_NUMBER": latex_escape(invoice_number),
            "INVOICE_DATE": latex_escape(args.invoice_date),
            "INVOICE_PERIOD": latex_escape(f"{start_date} to {end_date}"),
            "SERVICE_DESCRIPTION": latex_escape(env_values["SERVICE_DESCRIPTION"]),
            "HOURLY_RATE": format_money(hourly_rate),
            "HOURS_WORKED": format_hours(hours_worked),
            "TOTAL_DUE": format_money(total_due),
        }
    )
    invoice_data = {
        **env_values,
        "INVOICE_NUMBER": invoice_number,
        "INVOICE_DATE": args.invoice_date,
        "INVOICE_PERIOD": f"{start_date} to {end_date}",
        "SERVICE_DESCRIPTION": env_values["SERVICE_DESCRIPTION"],
        "HOURLY_RATE": format_money(hourly_rate),
        "HOURS_WORKED": format_hours(hours_worked),
        "TOTAL_DUE": format_money(total_due),
    }
    email_template = load_email_template(EMAIL_TEMPLATE_PATH)
    email_text = render_email_template(
        email_template,
        {
            "<Client Name>": env_values["EMPLOYER_NAME"],
            "<start–end date>": f"{start_date} to {end_date}",
            "<Your Name>": env_values["EMPLOYEE_NAME"],
            "<Your Phone Number>": env_values["EMPLOYEE_PHONE"],
        },
    )

    rendered_tex = render_template(raw_template, substitutions)
    tex_output_path.write_text(rendered_tex)
    email_output_path.write_text(email_text)

    if args.skip_pdf:
        print(f"Rendered LaTeX template to {tex_output_path}")
        print(f"Rendered email template to {email_output_path}")
        return

    build_pdf(tex_output_path, output_path, invoice_data)
    print(f"Generated invoice PDF at {output_path}")
    print(f"Rendered email template to {email_output_path}")

    if not args.keep_tex and tex_output_path.exists():
        tex_output_path.unlink()


if __name__ == "__main__":
    main()
