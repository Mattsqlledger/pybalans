"""Hulpfuncties voor de webapp-tests."""

import re
import time

import pyotp

from sqlledger_web import accounts, security

PASSWORD = "correct-horse-battery"


def make_user(db, login, *, sysadmin=False, totp=False, memberships=()):
    """Gebruiker met wachtwoord PASSWORD; geeft het TOTP-geheim terug (of None)."""
    uid = accounts.create_user(db, login, login.title(), is_sysadmin=sysadmin)
    secret = security.new_totp_secret() if totp else None
    db.execute("UPDATE app_user SET password_hash = %s, totp_secret = %s WHERE id = %s",
               (security.hash_password(PASSWORD), secret, uid))
    for company_id, role, *emp in memberships:
        db.execute("INSERT INTO membership VALUES (%s, %s, %s, %s)",
                   (uid, company_id, role, emp[0] if emp else login))
    return secret


def csrf(client, html=None):
    if html is None:
        html = client.get("/login").text
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "geen csrf-veld in pagina"
    return m.group(1)


def post(client, url, data, page=None, **kw):
    """POST met het csrf-token van ``page`` (standaard: de pagina zelf)."""
    html = client.get(page or url).text
    return client.post(url, data={"csrf": csrf(client, html), **data}, **kw)


def totp_now(secret, offset=0):
    return pyotp.TOTP(secret).at(time.time() + offset)


def login(client, login_name, secret=None, password=PASSWORD):
    r = post(client, "/login", {"login": login_name, "password": password})
    if secret and r.url.path == "/2fa":
        r = post(client, "/2fa", {"code": totp_now(secret)})
    return r


def form_values(html):
    """Ingevulde waarden van tekstvelden en gekozen opties (eenvoudig, voor de controlepagina van inlezen)."""
    from html import unescape
    out = {m.group(1): unescape(m.group(2)) for m in re.finditer(r'<input name="(\w+)" value="([^"]*)"', html)}
    for m in re.finditer(r'<select name="(\w+)">(.*?)</select>', html, re.S):
        sel = re.search(r'<option value="([^"]*)"[^>]* selected', m.group(2))
        if sel:
            out[m.group(1)] = unescape(sel.group(1))
    return out


def restore_backup(client, data, url="/systeem/back-up", filename="backup.sql.gz", templates=None, timeout=180,
                   **fields):
    """Back-up inlezen zoals de browser (v1.0.1): uploaden → controlescherm (vooringevulde waarden, aan te vullen
    met ``fields``; ``add_me`` standaard uit tenzij gegeven) → Inlezen → wachten tot de taak klaar is. Geeft de
    laatste respons: de foutpagina van een stap, of de resultaatpagina."""
    import io
    page = client.get(url).text
    files = {"bestand": (filename, io.BytesIO(data), "application/gzip")}
    if templates is not None:
        files["sjablonen"] = ("s.zip", io.BytesIO(templates), "application/zip")
    r = client.post(url, data={"csrf": csrf(client, page)}, files=files)
    if r.status_code != 200 or "/controle/" not in r.url.path:
        return r
    values = form_values(r.text)
    values.pop("csrf", None)
    values.update({k: v for k, v in fields.items() if v is not None})
    data = {"csrf": csrf(client, r.text), "action": "restore", **values}
    r = client.post(r.url.path, data=data)
    if r.status_code != 200 or "/taak/" not in r.url.path:
        return r
    job = r.url.path
    end = time.time() + timeout
    while time.time() < end:
        state = client.get(f"{job}/stand").json()
        if not state["running"]:
            break
        time.sleep(0.2)
    return client.get(job)


def nohelp(html):
    """Pagina zonder het helpblok "Hoe werkt het?" (templates/help/): voor toetsen die nagaan dat een tekst
    of knop níet op het scherm staat, terwijl de helptekst die wel noemt."""
    return re.sub(r'<details class="help">.*?</details>', "", html, flags=re.S)
