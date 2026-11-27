"""Een klein RGS-bestand in de vorm van het officiële ``RGS 3.8-def.xlsx`` (zelfde bladen en kolommen), voor tests.
Geen inhoud van de echte lijst: alleen een handvol codes met dezelfde opbouw, aangevuld tot boven de 100."""

import io

HEAD = ["Referentiecode", "Referentienummer", "Omschrijving (verkort)", "Omschrijving", "D/C", "Nivo", "Sortering",
        "Basis", "Inactief"]
CODES = [
    ("B", "Balans", "Balans", "", 1),
    ("W", "Winst- en verliesrekening", "Winst- en verliesrekening", "", 1),
    ("BLim", "Liquide middelen", "Liquide middelen", "D", 2),
    ("BLimKas", "Kasmiddelen", "Kasmiddelen", "D", 3),
    ("BLimKasKas", "Kas", "Kas", "D", 4),
    ("BLimBan", "Tegoeden op bankrekeningen", "Tegoeden op bankrekeningen", "D", 3),
    ("BLimBanRba", "Rekening-courant bank", "Rekening-courant bank groep 1", "D", 4),
    ("BVor", "Vorderingen", "Vorderingen", "D", 2),
    ("BVorDeb", "Vorderingen op handelsdebiteuren", "Vorderingen op handelsdebiteuren", "D", 3),
    ("BVorDebHad", "Handelsdebiteuren", "Handelsdebiteuren nominaal", "D", 4),
    ("BSch", "Kortlopende schulden", "Kortlopende schulden", "C", 2),
    ("BSchCre", "Schulden aan leveranciers", "Schulden aan leveranciers en handelskredieten", "C", 3),
    ("BSchCreHac", "Handelscrediteuren", "Handelscrediteuren nominaal", "C", 4),
    ("BSchBep", "Belastingen en premies", "Belastingen en premies sociale verzekeringen", "C", 3),
    ("BSchBepBtw", "Te betalen omzetbelasting", "Te betalen omzetbelasting", "C", 4),
    ("WOmz", "Netto-omzet", "Netto-omzet", "C", 2),
    ("WOmzNop", "Omzet producten", "Netto-omzet van geleverde producten", "C", 3),
    ("WOmzNopOlh", "Omzet hoog", "Omzet geleverde producten belast met hoog tarief", "C", 4),
    ("WBed", "Bedrijfslasten", "Bedrijfslasten", "D", 2),
    ("WBedHui", "Huisvestingskosten", "Huisvestingskosten", "D", 3),
    ("WBedHuiHuu", "Huur", "Huur onroerende zaken", "D", 4),
] + [(f"WBedOvk{i:03d}", f"Proefkosten {i}", f"Proefkosten nummer {i}", "D", 4) for i in range(1, 91)]


def make_xlsx(version="3.8", codes=CODES, mkb=("BLimBanRba", "BVorDebHad"), history=None, suffix="def",
              recap=None) -> bytes:
    """``suffix``: einde van het Totaal-blad (def, beta, …); ``recap``: eerste zin van een blad Recap (PB13)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = f"Totaal-RGS{version}-{suffix}" if suffix else f"Totaal-RGS{version}"
    if recap is not None:
        r = wb.create_sheet("Recap", 0)
        r.append([])
        r.append([None, recap])
    ws.append(["Referentie GrootboekSchema"])
    ws.append(HEAD)
    for n, (code, short, long, dc, level) in enumerate(codes, 1):
        ws.append([code, f"{n:07d}", short, long, dc or None, level, f"{n:05d}", 1 if n % 3 == 0 else 0, 0])
    m = wb.create_sheet(f"MKB-RGS{version}")
    m.append(["Referentiecode", "Omschrijving"])
    for code in mkb:
        m.append([code, ""])
    if history is None:
        history = {"3.7": [("BLimBanRbb", "BLimBanRba"), ("BVorDebHdb", "BVorDebHad")]}
    for old, pairs in history.items():
        v = wb.create_sheet(f"RGS{version}-versus-RGS{old}")
        v.append([f"RGS {version}", None, f"RGS {old}"])
        v.append(["Referentiecode", "Omschrijving", "Referentiecode"])
        for oldc, newc in pairs:
            v.append([newc, "", oldc])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
