"""Invented statements for the ingestion privacy and sign tests."""

from tuppence.ingest.textprep import pages_document

HEAD = [
    "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT",
    "Example Credit Union",
    "Mr Alex Example",
    "1 Example Road",
    "Exampleton EX1 2MP",
    "Statement period 01/10/2026 to 31/10/2026",
]
TABLE = "Date Description Paid out Paid in Balance"

PAGE1 = [
    "SYNTHETIC TEST STATEMENT - NOT A REAL DOCUMENT",
    "Example Credit Union",
    "Statement for Alex Example",
    "Mr Alex Example",
    "1 Example Road",
    "Exampleton EX1 2MP",
    "Account number 12345678   Sort code 12-34-56",
    "IBAN GB29 NWBK 6016 1331 9268 19",
    "Acc no 12345678",
    "Statement period 01/10/2026 to 31/10/2026",
    "Opening balance 1,000.00",
    "Money in 900.00",
    "Money out 120.00",
    "Closing balance 1,780.00",
    TABLE,
    "01/10/2026 GREENBASKET STORES 42.18 957.82",
    "03/10/2026 LITTLE CAFE 3.40 954.42",
    "05/10/2026 ACME PAYROLL LTD 900.00 1,854.42",
    "Page 1 of 2",
]
PAGE2 = [
    "Mr Alex Example",
    "A/C 12345678 S/C 12-34-56",
    "Card ending 4242",
    TABLE,
    "10/10/2026 CITY WATER 31.15 1,823.27",
    "12/10/2026 HOMEWARE DIRECT 43.27 1,780.00",
    "Customer number 99887766",
    "Alex Example",
    "Page 2 of 2",
]
SECRETS = [
    "Alex Example",
    "Example Road",
    "EX1 2MP",
    "12345678",
    "12-34-56",
    "GB29",
    "NWBK",
    "4242",
    "99887766",
    "1,000.00",
    "Closing balance",
    "Money out 120",
    "Money in 900",
    "Exampleton",
    "Example Credit Union",
]


def adversarial_doc(page1=PAGE1, page2=PAGE2):
    return pages_document([page1, page2], sha256="x", kind="pdf")
