"""
Test document generator for OFAC Sanctions Screener UI.

Produces PDF files containing text that references sanctioned entities,
vessels, and organisations — suitable for uploading to the screener UI
to exercise the Vertex AI entity extraction + BigQuery SDN matching pipeline.

Usage:
    pip install fpdf2
    python generate.py

Output: PDF files written to the same directory as this script.
"""

import os
from fpdf import FPDF

OUT_DIR = os.path.dirname(os.path.abspath(__file__))


def create_test_pdf(filename: str, title: str, content: str) -> None:
    pdf = FPDF()
    pdf.set_margins(left=20, top=20, right=20)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(8)

    # Body — Courier for that document look
    pdf.set_font("Courier", size=10)
    w = pdf.epw  # effective page width after margins
    for line in content.split("\n"):
        if line.strip() == "":
            pdf.ln(5)
        else:
            pdf.multi_cell(w, 5.5, line)

    path = os.path.join(OUT_DIR, filename)
    pdf.output(path)
    print(f"  Created: {path}")


# ── CASE 1: Bill of Lading — Vessel / entity match ────────────────────────────
# BAIKAL (IMO 9158591) and Northern Shipping Company are on the SDN list
# under Russia-related programmes.
bol_content = """\
BILL OF LADING  #BL-RU-9928
----------------------------------------------------------------
DATE:       14 February 2026

SHIPPER:    Global Wood Exports LLC
            12 Timber Lane, Arkhangelsk, Russia

CONSIGNEE:  Alpha Trade Port Services
            Istanbul, Turkey

NOTIFY:     Joint Stock Company Northern Shipping Company (NSC)
            119 Naberezhnaya Sev. Dviny
            Arkhangelsk, 163000, Russia

VESSEL:     BAIKAL   (IMO 9158591)
PORT OF LOADING:    Arkhangelsk, RU
PORT OF DISCHARGE:  Istanbul, TR

DESCRIPTION OF GOODS:
  500 Metric Tons of Sawn Timber
  Harmonized Code: 4407.11

FREIGHT:    PREPAID
CARRIER:    NSC Logistics Division
"""

# ── CASE 2: Commercial Invoice — alias / fuzzy match ──────────────────────────
# Hizballah Support Network and Jammal Trust Bank SAL appear in the SDN list
# under SDGT and Lebanon-related programmes.
invoice_content = """\
COMMERCIAL INVOICE  #INV-2026-044
DATE: 15 February 2026
----------------------------------------------------------------
VENDOR:   Middle East Media Logistics
REMIT TO: Hizballah Support Network
          Beirut, Lebanon

BILL TO:  International Relief Org (Main Office)
          Geneva, Switzerland

LINE ITEMS:
  1. Specialized Broadcasting Equipment (10 units) ...... $55,000.00
  2. Installation & Commissioning Services .............. $ 5,000.00
                                          TOTAL DUE:     $60,000.00

PAYMENT INSTRUCTIONS:
  Bank:   Jammal Trust Bank SAL
  Ref:    Account 992-00
  IBAN:   LB62 0999 0000 0001 0019 0122 9114

AUTHORISED SIGNATORY: ___________________________
"""

# ── CASE 3: Professional Services Invoice — entity match ──────────────────────
# Al-Qatirji Company is listed under Syria / NPWMD programmes.
syria_content = """\
INVOICE FOR CONSULTING SERVICES
DATE: 18 February 2026
----------------------------------------------------------------
FROM: Al-Qatirji Company
      Ar-Raqqa, Syria

TO:   Euro-Med Logistics GmbH
      Speicherstadt 4, Hamburg, Germany

SERVICE DESCRIPTION:
  Logistics coordination for regional fuel transport (Q1 2026).
  Compliance and transit facilitation fee.

  - Route planning & carrier liaison .................. 80,000 EUR
  - Cross-border documentation ........................ 40,000 EUR
                                        TOTAL:        120,000 EUR

PAYMENT INSTRUCTIONS:
  Please transfer to Ar-Raqqa Commercial Branch.
  SWIFT: SYRIXXXX
  Reference: AQ-Q1-2026
"""

# ── CASE 4: Wire Transfer Instruction — individual match (phonetic) ────────────
# Uses a phonetic variant of USAMA BIN LADIN to test the SOUNDEX / fuzzy path.
wire_content = """\
WIRE TRANSFER INSTRUCTION
DATE: 19 February 2026
----------------------------------------------------------------
ORIGINATING BANK:   First Gulf International Bank
ACCOUNT HOLDER:     Osama Bin Laden Foundation for Relief
ACCOUNT NUMBER:     40-0012-9981
SORT CODE:          20-14-53

BENEFICIARY:        Al-Haramain Islamic Foundation
BENEFICIARY BANK:   Dubai Islamic Bank
BENEFICIARY ACCT:   AE07 0331 2345 6789 0123 456
SWIFT:              DUIBAEAD

AMOUNT:             USD 25,000.00
REFERENCE:          RELIEF-Q1-2026
PURPOSE:            Humanitarian aid disbursement - Q1 tranche

AUTHORISED BY:      ___________________________
"""

# ── CASE 5: Clean Document — no sanctioned entities ───────────────────────────
# Should return document_clear: true. Useful for confirming no false positives.
clean_content = """\
PURCHASE ORDER  #PO-2026-0881
DATE: 20 February 2026
----------------------------------------------------------------
BUYER:    Northgate Procurement Ltd
          14 Commerce Street, Dublin, Ireland

SELLER:   Pacific Office Supplies Co.
          88 Industrial Park, Singapore 628346

ITEMS:
  1. A4 Copier Paper, 80 gsm, 5 pallets ............... SGD 3,200
  2. Ergonomic Office Chairs (20 units) ............... SGD 8,600
  3. Standing Desks (10 units) ....................... SGD 11,400
                                        TOTAL:       SGD 23,200

DELIVERY:   CIF Dublin Port, Incoterms 2020
PAYMENT:    Net 30 days from invoice date
CONTACT:    procurement@northgate.ie  |  +353 1 800 0000

All goods comply with applicable EU import regulations.
No dual-use items are included in this order.
"""


if __name__ == "__main__":
    print("Generating test PDFs...\n")
    create_test_pdf(
        "Test_01_Bill_Of_Lading.pdf",
        "BILL OF LADING",
        bol_content,
    )
    create_test_pdf(
        "Test_02_Fuzzy_Invoice.pdf",
        "COMMERCIAL INVOICE",
        invoice_content,
    )
    create_test_pdf(
        "Test_03_Syria_Invoice.pdf",
        "SERVICE INVOICE",
        syria_content,
    )
    create_test_pdf(
        "Test_04_Wire_Transfer.pdf",
        "WIRE TRANSFER INSTRUCTION",
        wire_content,
    )
    create_test_pdf(
        "Test_05_Clean_PO.pdf",
        "PURCHASE ORDER (CLEAN)",
        clean_content,
    )
    print("\nDone. Upload any PDF to https://sanctions.krozario.demo.altostrat.com")
