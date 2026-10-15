"""Build a SYNTHETIC sample bid package (and its answer key) so the whole
pipeline, tests and evaluation can run before any real package is downloaded.

It is clearly marked "SYNTHETIC SAMPLE - NOT A REAL BID" on every page.
Never show it to anyone as a real package: the demo uses real public packages.

    python -m bidbrief.sample            # writes data/packages/sample-mapleford
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pymupdf

from .config import get_settings

PACKAGE_ID = "sample-mapleford"
W, H = 612, 792
MARGIN = 60
MARK = "SYNTHETIC SAMPLE - NOT A REAL BID - Mapleford Contract W-2026-04"

BID_ITEMS = [
    ("1", "Mobilization and Demobilization, not to exceed 3% of Total Base Bid", "1", "LS"),
    ("2", "Police Details (Allowance)", "60,000", "$"),
    ("3", "Traffic Management Plan and Temporary Signage", "1", "LS"),
    ("4", "12-inch Ductile Iron Water Main, Class 52, including fittings", "3,120", "LF"),
    ("5", "8-inch Ductile Iron Water Main, Class 52, including fittings", "1,730", "LF"),
    ("6", "6-inch Ductile Iron Hydrant Branch", "180", "LF"),
    ("7", "12-inch Gate Valve and Box", "9", "EA"),
    ("8", "8-inch Gate Valve and Box", "7", "EA"),
    ("9", "Fire Hydrant Assembly, complete", "12", "EA"),
    ("10", "1-inch Copper Water Service, Main to Curb Stop", "64", "EA"),
    ("11", "1-inch Copper Water Service, Curb Stop to House", "1,150", "LF"),
    ("12", "Cut and Cap Existing Water Main", "6", "EA"),
    ("13", "Temporary Water Service, complete", "1", "LS"),
    ("14", "Test Pits for Existing Utilities", "20", "EA"),
    ("15", "Rock Excavation and Disposal", "150", "CY"),
    ("16", "Dewatering", "1", "LS"),
    ("17", "Gravel Borrow for Trench Backfill", "900", "CY"),
    ("18", "Disposal of Contaminated Soil (Allowance)", "200", "TON"),
    ("19", "Temporary Trench Pavement, 3-inch", "2,450", "SY"),
    ("20", "Permanent Trench Pavement, 4-inch HMA", "2,450", "SY"),
    ("21", "Pavement Milling and 1.5-inch Overlay, curb to curb", "9,800", "SY"),
    ("22", "Granite Curb Reset", "320", "LF"),
    ("23", "Loam and Seed", "1,100", "SY"),
    ("24", "Pressure Testing, Disinfection and Flushing", "1", "LS"),
]
ALTERNATES = [
    ("A-1", "Replace 2-inch Galvanized Main on Birch Lane with 8-inch DI", "420", "LF"),
    ("A-2", "Additional Hydrant Assembly", "2", "EA"),
]


class Doc:
    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page: pymupdf.Page | None = None
        self.y = 0.0
        self.section_pages: dict[str, int] = {}

    @property
    def pno(self) -> int:
        return len(self.doc)

    def new_page(self, landscape: bool = False) -> None:
        w, h = (H, W) if landscape else (W, H)
        self.page = self.doc.new_page(width=w, height=h)
        self.y = MARGIN
        self.page.insert_text((MARGIN, 30), MARK, fontsize=7, fontname="helv", color=(0.6, 0, 0))
        self.page.insert_text((w - MARGIN - 40, h - 25), f"Page {self.pno}", fontsize=8, fontname="helv")

    def heading(self, text: str, size: float = 14) -> None:
        assert self.page is not None
        self.page.insert_text((MARGIN, self.y + size), text, fontsize=size, fontname="hebo")
        self.y += size + 14

    def para(self, text: str, size: float = 10.5, bold: bool = False) -> None:
        assert self.page is not None
        width = self.page.rect.width - 2 * MARGIN
        rect = pymupdf.Rect(MARGIN, self.y, MARGIN + width, self.page.rect.height - MARGIN)
        rc = self.page.insert_textbox(rect, text, fontsize=size, fontname="hebo" if bold else "helv", lineheight=1.3)
        if rc < 0:
            raise ValueError(f"Paragraph does not fit on page {self.pno}: {text[:60]}")
        used = rect.height - rc
        self.y += used + 8

    def section_page(self, key: str) -> None:
        self.section_pages.setdefault(key, self.pno)

    def table(self, headers: list[str], rows: list[tuple[str, ...]], widths: list[float], size: float = 9) -> None:
        assert self.page is not None
        x0 = MARGIN
        row_h = 26

        def draw_row(cells, bold=False, h=row_h):
            font = "hebo" if bold else "helv"
            # grow the row until every cell's text fits (insert_textbox writes nothing if it does not fit)
            while True:
                fits = all(
                    pymupdf.get_text_length(cell, fontname=font, fontsize=size) <= 0.9 * (w - 6) * max(1, int((h - 5) / (size * 1.25)))
                    for cell, w in zip(cells, widths))
                if fits or h > 120:
                    break
                h += 12
            x = x0
            for cell, w in zip(cells, widths):
                r = pymupdf.Rect(x, self.y, x + w, self.y + h)
                self.page.draw_rect(r, color=(0, 0, 0), width=0.6)
                rc = self.page.insert_textbox(r + (3, 3, -3, -2), cell, fontsize=size, fontname=font)
                if rc < 0:
                    raise ValueError(f"Table cell does not fit: {cell!r}")
                x += w
            self.y += h
        if headers:
            draw_row(headers, bold=True, h=30)
        for row in rows:
            draw_row(row)

    def save(self, path: Path) -> None:
        self.doc.save(path, garbage=3, deflate=True)


GENERAL_TEXT = (
    "The Contractor shall furnish all labor, materials, equipment and services necessary to complete the Work in "
    "accordance with the Contract Documents. The Engineer shall decide all questions about the quality and "
    "acceptability of materials furnished and work performed. The Contractor shall keep on the site a competent "
    "superintendent during the progress of the Work. Shop drawings shall be submitted in time to allow review "
    "without delay to the Work. The Contractor shall protect all existing improvements and restore any damage at no "
    "additional cost to the Owner."
)


def build(out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = Doc()
    key_pages: dict[str, int] = {}

    # 1 cover
    d.new_page()
    d.y = 200
    d.heading("TOWN OF MAPLEFORD, MASSACHUSETTS", 18)
    d.heading("BOARD OF PUBLIC WORKS - WATER DIVISION", 13)
    d.y += 30
    d.heading("CONTRACT DOCUMENTS AND SPECIFICATIONS", 16)
    d.heading("ELM STREET AND OAK AVENUE WATER MAIN REPLACEMENT", 14)
    d.heading("CONTRACT NO. W-2026-04", 13)
    d.y += 40
    d.para("SYNTHETIC SAMPLE PACKAGE FOR SOFTWARE TESTING ONLY. This is not a real bid and is not issued by any town. "
           "All names, places, dates and quantities are invented.", bold=True)

    # 2 table of contents
    d.new_page()
    d.heading("TABLE OF CONTENTS")
    for title, pg in [("Invitation for Bids", 3), ("Instructions to Bidders", 4), ("Bid Form", 6),
                      ("Agreement", 9), ("General Conditions", 10), ("Supplementary Conditions", 12),
                      ("Prevailing Wage Rates", 15), ("Technical Specifications", 16), ("List of Drawings", 22)]:
        d.para(f"{title} " + "." * 60 + f" {pg}", size=10)

    # 3 invitation
    d.new_page()
    d.section_page("Invitation for Bids")
    key_pages["invitation"] = d.pno
    d.heading("INVITATION FOR BIDS")
    d.para("The Town of Mapleford, acting through its Board of Public Works (the Owner), invites sealed bids for the "
           "Elm Street and Oak Avenue Water Main Replacement, Contract No. W-2026-04, in Mapleford, Massachusetts.")
    d.para("Sealed bids will be received at the Office of the DPW Director, 40 Center Street, Mapleford, MA until "
           "11:00 AM on Thursday, November 19, 2026, at which time they will be publicly opened and read aloud.")
    d.para("The Work consists of replacing approximately 4,850 linear feet of existing 6-inch cast iron water main on "
           "Elm Street and Oak Avenue with 12-inch and 8-inch ductile iron water main, together with new gate valves, "
           "fire hydrants, water services, temporary water, trench pavement and final paving.")
    d.para("A pre-bid conference will be held at 10:00 AM on Tuesday, November 3, 2026 at the DPW Garage, 12 Mill "
           "Road, Mapleford. Attendance is not mandatory.")
    d.para("Each bid must be accompanied by bid security in the amount of five percent (5%) of the value of the bid, "
           "in the form of a bid bond, certified check, or treasurer's or cashier's check.")
    d.para("This Contract is subject to M.G.L. Chapter 30, Section 39M and to the Massachusetts prevailing wage law, "
           "M.G.L. Chapter 149, Sections 26 to 27H. The Owner reserves the right to reject any or all bids.")

    # 4-5 instructions to bidders
    d.new_page()
    d.section_page("Instructions to Bidders")
    key_pages["itb"] = d.pno
    d.heading("INSTRUCTIONS TO BIDDERS")
    d.para("1. Bids shall be submitted on the Bid Form furnished in these Contract Documents. Unit prices shall be "
           "written in both words and figures. In case of a discrepancy, the unit price written in words governs.")
    d.para("2. Addenda will be issued to all plan holders of record. Each Bidder shall acknowledge all addenda on the "
           "Bid Form.")
    d.para("3. Award will be made to the lowest responsible and eligible bidder on the basis of the Total Base Bid. "
           "The Owner may award any or all Bid Alternates.")
    d.new_page()
    key_pages["bonds"] = d.pno
    d.heading("INSTRUCTIONS TO BIDDERS (continued)", 12)
    d.para("4. The successful Bidder shall furnish a performance bond and a labor and materials payment bond, each in "
           "the amount of one hundred percent (100%) of the Contract Price, issued by a surety licensed in "
           "Massachusetts.")
    d.para("5. Bidders shall examine the site and the Contract Documents. Quantities shown on the Bid Form are "
           "estimates for comparing bids only; payment will be made for actual quantities installed.")

    # 6-8 bid form
    d.new_page()
    d.section_page("Bid Form")
    key_pages["bid_form"] = d.pno
    item_pages: dict[str, int] = {}
    d.heading("BID FORM")
    d.para("Contract No. W-2026-04. The undersigned proposes to perform the Work at the following unit prices. "
           "Unit prices in words and figures.", size=9.5)
    headers = ["Item No.", "Est. Quantity", "Unit", "Item Description", "Unit Price", "Total"]
    widths = [45, 62, 38, 205, 70, 72]
    first = BID_ITEMS[:16]
    d.table(headers, [(a, q, u, desc, "", "") for a, desc, q, u in first], widths)
    for a, *_ in first:
        item_pages[a] = d.pno
    d.new_page()
    d.heading("BID FORM (continued)", 12)
    rest = BID_ITEMS[16:]
    d.table([], [(a, q, u, desc, "", "") for a, desc, q, u in rest], widths)  # continuation: no header row
    for a, *_ in rest:
        item_pages[a] = d.pno
    d.y += 10
    d.para("TOTAL BASE BID (Items 1 through 24): ______________________ Dollars ($______________)", size=9.5)
    d.new_page()
    d.heading("BID FORM - BID ALTERNATES", 12)
    d.para("Bid Alternates are not part of the Total Base Bid.", size=9.5)
    for a, desc, q, u in ALTERNATES:
        d.para(f"{a}    {q}    {u}    {desc}", size=10)
        item_pages[a] = d.pno
    d.para("Acknowledge Addenda: No. ____  No. ____", size=9.5)

    # 9 agreement
    d.new_page()
    d.section_page("Agreement")
    d.heading("AGREEMENT")
    d.para("This Agreement is made between the Town of Mapleford (the Owner) and the Contractor named below for the "
           "Elm Street and Oak Avenue Water Main Replacement. The Contractor shall complete the Work in accordance "
           "with the Contract Documents for the unit prices stated in the Bid Form.")

    # 10-11 general conditions
    d.new_page()
    d.section_page("General Conditions")
    d.heading("GENERAL CONDITIONS")
    d.para(GENERAL_TEXT)
    d.para("Differing site conditions: if the Contractor finds subsurface conditions at the site materially different "
           "from those shown in the Contract Documents, the Contractor shall notify the Engineer in writing before "
           "disturbing them, and an equitable adjustment may be made.")
    d.new_page()
    d.heading("GENERAL CONDITIONS (continued)", 12)
    d.para("Payment: the Owner shall pay the Contractor within thirty (30) days after approval of each monthly "
           "application for payment, less retainage as provided in the Supplementary Conditions.")
    d.para(GENERAL_TEXT)

    # 12-14 supplementary conditions
    d.new_page()
    d.section_page("Supplementary Conditions")
    key_pages["sc"] = d.pno
    d.heading("SUPPLEMENTARY CONDITIONS")
    d.para("SC-1 Contract Time. The Contractor shall substantially complete the Work within two hundred ten (210) "
           "consecutive calendar days from the date of the Notice to Proceed.")
    key_pages["time"] = d.pno
    d.para("SC-2 Liquidated Damages. For each calendar day that the Work remains incomplete after the Contract Time, "
           "the Contractor shall pay the Owner liquidated damages of One Thousand Five Hundred Dollars ($1,500.00) "
           "per calendar day.")
    key_pages["ld"] = d.pno
    d.para("SC-3 Retainage. The Owner will retain five percent (5%) of each progress payment until substantial "
           "completion.")
    key_pages["retainage"] = d.pno
    d.para("SC-4 Working Hours. Work shall be performed between 7:00 AM and 5:00 PM, Monday through Friday. No work "
           "is allowed on weekends or legal holidays without written approval of the Owner.")
    key_pages["hours"] = d.pno
    d.new_page()
    d.heading("SUPPLEMENTARY CONDITIONS (continued)", 12)
    d.para("SC-5 Night Work. Work within the Route 110 intersection shall be performed at night between 9:00 PM and "
           "5:00 AM. Only one lane of Route 110 may be closed at any time.")
    key_pages["night"] = d.pno
    d.para("SC-6 Police Details. Uniformed police officers are required for all work within public ways. Police "
           "details will be paid under the Police Details allowance item.")
    key_pages["police"] = d.pno
    d.para("SC-7 Paving Season. No permanent trench pavement shall be placed after November 15 or before April 15 "
           "without the written approval of the Engineer.")
    key_pages["paving"] = d.pno
    d.para("SC-8 Subsurface Conditions. The Contractor shall make its own investigation of subsurface conditions. "
           "No claim for additional compensation will be allowed for rock, ledge or boulders encountered in the "
           "trench.")
    key_pages["rock_sc"] = d.pno
    d.new_page()
    d.heading("SUPPLEMENTARY CONDITIONS (continued)", 12)
    d.para("SC-9 Insurance. In addition to the limits in the General Conditions, the Contractor shall carry umbrella "
           "liability insurance of not less than $5,000,000 per occurrence, naming the Town of Mapleford as an "
           "additional insured.")
    key_pages["insurance"] = d.pno
    d.para("SC-10 Utilities. The locations of existing utilities shown on the Drawings are approximate. The "
           "Contractor shall excavate test pits to locate utilities before laying pipe.")
    key_pages["utilities"] = d.pno

    # 15 wage rates
    d.new_page()
    d.section_page("Wage Rates")
    key_pages["wages"] = d.pno
    d.heading("PREVAILING WAGE RATES")
    d.para("The Massachusetts prevailing wage rate schedule issued for this project applies to all work. The "
           "Contractor shall submit certified weekly payrolls to the Owner. Apprentices must be registered with the "
           "Division of Apprentice Standards.")

    # 16-21 specifications
    d.new_page()
    key_pages["spec_earth"] = d.pno
    d.heading("SECTION 31 23 00 - EXCAVATION AND FILL")
    d.para("1.01 Rock Excavation. Rock excavation is paid at the unit price bid for Item 15, measured in cubic yards "
           "within the pay limits shown on the Drawings.")
    key_pages["rock_spec"] = d.pno
    d.para("1.02 Dewatering. Groundwater is expected near Station 12+00 to 18+00. The Contractor shall keep "
           "excavations free of water and shall discharge only through a sediment control device.")
    key_pages["groundwater"] = d.pno
    d.para("1.03 Compaction. Trench backfill shall be compacted to at least 95 percent of maximum dry density. The "
           "Contractor shall pay for one compaction test for every 200 linear feet of trench.")
    key_pages["compaction"] = d.pno
    d.new_page()
    d.heading("SECTION 31 23 00 - EXCAVATION AND FILL (continued)", 12)
    d.para(GENERAL_TEXT)
    d.new_page()
    key_pages["spec_water"] = d.pno
    d.heading("SECTION 33 11 00 - WATER UTILITY DISTRIBUTION PIPING")
    d.para("3.01 Pressure Test. Each section of new main shall be pressure tested at 150 psi for two (2) hours. "
           "Leakage shall not exceed the allowable amount in AWWA C600.")
    key_pages["pressure"] = d.pno
    d.para("3.02 Disinfection. New mains shall be disinfected in accordance with AWWA C651. Two consecutive sets of "
           "bacteriological samples taken 24 hours apart must pass before the main is placed in service.")
    key_pages["disinfection"] = d.pno
    d.para("3.03 Ductile iron pipe shall be Class 52, cement lined, with push-on joints, conforming to AWWA C151.")
    d.new_page()
    key_pages["spec_soil"] = d.pno
    d.heading("SECTION 02 61 00 - CONTAMINATED SOIL")
    d.para("1.01 Soil testing at three locations on Oak Avenue found petroleum compounds above reportable "
           "concentrations. Excavated soil in these areas shall be stockpiled, tested and disposed of at a licensed "
           "facility. Disposal is paid under the allowance in Item 18.")
    key_pages["contaminated"] = d.pno
    d.new_page()
    key_pages["spec_traffic"] = d.pno
    d.heading("SECTION 34 71 13 - TRAFFIC CONTROL")
    d.para("1.01 The Contractor shall maintain access for residents and emergency vehicles at all times. Detours "
           "require the approval of the Mapleford Police Department at least seven (7) days in advance.")
    key_pages["detours"] = d.pno
    d.new_page()
    d.heading("SECTION 34 71 13 - TRAFFIC CONTROL (continued)", 12)
    d.para(GENERAL_TEXT)

    # 22 drawings
    d.new_page()
    d.section_page("Drawings List")
    d.heading("LIST OF DRAWINGS")
    for s_ in ["G-1 Cover Sheet", "G-2 General Notes", "C-1 Elm Street Plan and Profile Sta 0+00 to 12+00",
               "C-2 Elm Street Plan and Profile Sta 12+00 to 24+00", "C-3 Oak Avenue Plan", "C-4 Details"]:
        d.para(s_, size=10)

    main_pdf = out_dir / "01_Contract_Documents.pdf"
    d.save(main_pdf)

    # Addendum 1: a normal text PDF that changes the bid date and a quantity
    a = Doc()
    a.new_page()
    a.section_page("Addendum")
    a.heading("ADDENDUM NO. 1")
    a.para("Elm Street and Oak Avenue Water Main Replacement, Contract No. W-2026-04. Issued November 9, 2026.")
    a.para("This Addendum forms part of the Contract Documents and modifies them as follows.")
    a.para("Item 1. The bid opening is changed to 2:00 PM on Tuesday, November 24, 2026. All other terms remain.")
    a.para("Item 2. Bid Form Item 7, 12-inch Gate Valve and Box: the estimated quantity is changed from 9 EA to 11 EA.")
    a.para("Item 3. Question: Is the pre-bid conference mandatory? Answer: No.")
    a.save(out_dir / "02_Addendum_1.pdf")

    # Addendum 2: a scanned (image-only) page, to test OCR
    a2 = Doc()
    a2.new_page()
    a2.heading("ADDENDUM NO. 2")
    a2.para("Elm Street and Oak Avenue Water Main Replacement, Contract No. W-2026-04.")
    a2.para("Item 1. Saturday work between 8:00 AM and 4:00 PM is allowed with 48 hours notice to the Engineer.")
    png = a2.doc[0].get_pixmap(dpi=200, colorspace=pymupdf.csGRAY).tobytes("png")
    scan = pymupdf.open()
    sp = scan.new_page(width=W, height=H)
    sp.insert_image(sp.rect, stream=png)
    scan.save(out_dir / "03_Addendum_2_scanned.pdf")

    (out_dir / "package.json").write_text(json.dumps({
        "title": "SAMPLE (synthetic) - Mapleford Water Main Replacement",
        "synthetic": True,
        "note": "Generated by python -m bidbrief.sample. Not a real bid package.",
    }, indent=1))
    return {"key_pages": key_pages, "item_pages": item_pages, "main": main_pdf.name,
            "addendum": "02_Addendum_1.pdf", "scanned": "03_Addendum_2_scanned.pdf"}


def write_answer_key(key_dir: Path, info: dict) -> None:
    key_dir.mkdir(parents=True, exist_ok=True)
    main, add = info["main"], info["addendum"]
    kp, ip = info["key_pages"], info["item_pages"]
    with open(key_dir / "bid_items.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["item_no", "description", "quantity", "unit", "page"])
        for a, desc, q, u in BID_ITEMS + ALTERNATES:
            w.writerow([a, desc, q, u, ip[a]])
    with open(key_dir / "brief.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "value", "file", "pages"])
        w.writerow(["project_name", "Elm Street and Oak Avenue Water Main Replacement", main, kp["invitation"]])
        w.writerow(["owner", "Town of Mapleford", main, kp["invitation"]])
        w.writerow(["location", "Mapleford", main, kp["invitation"]])
        w.writerow(["scope", "4,850 linear feet", main, kp["invitation"]])
        w.writerow(["bid_due", "November", f"{main};{add}", f"{kp['invitation']};1"])
        w.writerow(["pre_bid_meeting", "November 3, 2026", main, kp["invitation"]])
        w.writerow(["completion_time", "210", main, kp["time"]])
        w.writerow(["bid_security", "5%", main, kp["invitation"]])
        w.writerow(["bonds", "100%", main, kp["bonds"]])
    qs = [
        ("What are the liquidated damages?", 1, "1,500", kp["ld"]),
        ("What testing is required for the new water main?", 1, "150 psi", kp["pressure"]),
        ("Are there night work restrictions?", 1, "9:00 PM", kp["night"]),
        ("How much retainage is held?", 1, "5%", kp["retainage"]),
        ("What is the contract time?", 1, "210", kp["time"]),
        ("What are the working hours?", 1, "7:00 AM", kp["hours"]),
        ("What umbrella insurance is required?", 1, "5,000,000", kp["insurance"]),
        ("How are police details paid?", 1, "allowance", kp["police"]),
        ("When can permanent paving be placed?", 1, "November 15", kp["paving"]),
        ("What compaction is required for trench backfill?", 1, "95", kp["compaction"]),
        ("How must new mains be disinfected?", 1, "C651", kp["disinfection"]),
        ("Is groundwater expected?", 1, "12+00", kp["groundwater"]),
        ("What pipe class is required?", 1, "Class 52", kp["spec_water"]),
        ("How much notice is needed for a detour?", 1, "seven", kp["detours"]),
        ("Is the pre-bid conference mandatory?", 1, "not mandatory", kp["invitation"]),
        ("What is the engineer's cost estimate for this project?", 0, "", ""),
        ("Is there a DBE or MBE participation goal?", 0, "", ""),
        ("What brand of fire hydrant must be used?", 0, "", ""),
        ("Who is the project manager at the engineering firm?", 0, "", ""),
        ("Is there an early completion bonus?", 0, "", ""),
    ]
    with open(key_dir / "questions.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "question", "answerable", "expected_contains", "pages"])
        for i, (q, ans, exp, pg) in enumerate(qs, 1):
            w.writerow([f"q{i:02d}", q, ans, exp, pg])
    with open(key_dir / "risks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["category", "pages", "note"])
        for cat, pages, note in [
            ("Liquidated damages", kp["ld"], "$1,500 per day"),
            ("Schedule", f"{kp['time']};{kp['hours']};{kp['paving']}", "210 days, working hours, paving season"),
            ("Payment", kp["retainage"], "5% retainage"),
            ("Bonds and insurance", kp["insurance"], "$5M umbrella"),
            ("Site conditions", f"{kp['rock_sc']};{kp['groundwater']};{kp['contaminated']};{kp['utilities']}",
             "rock, groundwater, contaminated soil, utilities"),
            ("Traffic", f"{kp['night']};{kp['police']};{kp['detours']}", "night work, police, detours"),
            ("Testing", f"{kp['pressure']};{kp['disinfection']};{kp['compaction']}", "pressure, disinfection, compaction"),
            ("Responsibility shifts", kp["rock_sc"], "no claim for rock"),
            ("Labor", kp["wages"], "prevailing wage, apprentices"),
        ]:
            w.writerow([cat, pages, note])
    (key_dir / "README.md").write_text(
        "Answer key for the SYNTHETIC sample package. Pages are 1-based page numbers in the PDF file.\n"
        "Lists of pages use ';'. For brief fields, `value` is a short text that a correct answer must contain.\n")


def main() -> None:
    s = get_settings()
    out = s.packages_dir / PACKAGE_ID
    info = build(out)
    write_answer_key(s.answer_keys_dir / PACKAGE_ID, info)
    print(f"Synthetic sample written to {out}")
    print(f"Answer key written to {s.answer_keys_dir / PACKAGE_ID}")


if __name__ == "__main__":
    main()
