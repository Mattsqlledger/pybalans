"""Mailen (fase 4 stap 1, D9): Beheer > E-mail en het mailscherm van een document.

Het mailscherm volgt io.pl ``e_mail``: aan/cc/bcc uit de relatie, onderwerp en bericht, het document als
PDF-bijlage. Na verzenden zoals ``print_form`` met media=email: vinkje "gemaild" in ``status``, een
notitie ``[email]`` onder de interne notities, audittrail ``emailed`` en het logboek van de app.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from starlette.datastructures import FormData

from sqlledger.dates import format_date
from sqlledger.printdata import add_intnote, mark_status

from . import mailer
from . import doctypes
from .i18n import N_, gettext as _
from .mailer import MailError, MailSettings
from .routes_docs import filename, load, renderer
from .web import Ctx, Redirect, csrf_form, ctx_company, render, require_role

router = APIRouter()

#: documentsoorten in Beheer > E-mail (volgorde van doctypes)
FORMNAMES = doctypes.TEMPLATE_KINDS


# ================================================================== Beheer > E-mail

def _settings_page(ctx: Ctx, ms: MailSettings, status: int = 200, **kw):
    lang = ctx.request.query_params.get("taal") or kw.pop("lang", None) or ctx.company.get("doc_language") or "nl"
    if lang not in mailer.DEFAULT_TEXTS:
        lang = "nl"
    return render(ctx, "mail/settings.html", status, ms=ms, doc_lang=lang, forms=FORMNAMES,
                  label=doctypes.long_label, defaults=mailer.DEFAULT_TEXTS[lang],
                  placeholders=list(mailer.PLACEHOLDERS)[0 if lang == "nl" else 1::2],
                  **kw)


@router.get("/beheer/email")
def settings_form(ctx: Ctx = Depends(ctx_company)):
    require_role(ctx, "admin")
    return _settings_page(ctx, mailer.load(ctx.conn, ctx.company["id"]))


def _g(form: FormData, key: str) -> str:
    return str(form.get(key, "") or "").strip()


@router.post("/beheer/email")
def settings_save(ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    require_role(ctx, "admin")
    action = _g(form, "action")
    ms = mailer.load(ctx.conn, ctx.company["id"])
    lang = _g(form, "lang") if _g(form, "lang") in mailer.DEFAULT_TEXTS else "nl"
    if action == "test":
        return _send_test(ctx, ms, lang)
    if action != "save":
        raise HTTPException(400, _("Onbekende actie."))
    ms.mode, ms.host, ms.security = _g(form, "mode"), _g(form, "host"), _g(form, "security")
    ms.username, ms.from_name = _g(form, "username"), _g(form, "from_name")
    ms.from_address, ms.reply_to = _g(form, "from_address"), _g(form, "reply_to")
    try:
        ms.port = int(_g(form, "port") or (465 if ms.security == "ssl" else 587))
    except ValueError:
        return _settings_page(ctx, ms, 400, lang=lang, error=_("Ongeldige poort."))
    texts = {k: dict(v) for k, v in (ms.texts or {}).items()}
    own = {}
    for formname in FORMNAMES:
        entry = {}
        for key in ("subject", "body"):
            value = str(form.get(f"{key}_{formname}", "") or "").replace("\r\n", "\n").strip()
            # gelijk aan de standaardtekst = geen eigen tekst (dan volgt een latere verbetering vanzelf)
            if value and value != mailer.DEFAULT_TEXTS[lang][formname][key]:
                entry[key] = value[:5000]
        if entry:
            own[formname] = entry
    texts[lang] = own
    ms.texts = texts
    try:
        if form.get("clear_password"):
            ms.password_enc = ""
        elif str(form.get("password", "") or ""):
            ms.password_enc = mailer.encrypt(ctx.settings, str(form.get("password")))
        mailer.validate(ms)
        if any(any(c in v for c in "\r\n") for e in own.values() for k, v in e.items() if k == "subject"):
            raise MailError(N_("Regeleinden zijn niet toegestaan in adressen en het onderwerp."))
    except MailError as exc:
        return _settings_page(ctx, ms, 400, lang=lang, error=exc.text())
    mailer.save(ctx.conn, ms)
    ctx.log("mail_settings_saved", mode=ms.mode, host=ms.host, from_address=ms.from_address)
    raise Redirect(f"/beheer/email?taal={lang}&ok=saved")


def _send_test(ctx: Ctx, ms: MailSettings, lang: str):
    to = ctx.user.get("email") or ""
    try:
        if not to:
            raise MailError(N_("Vul eerst uw eigen e-mailadres in bij Mijn instellingen."))
        out = mailer.compose(to, "", "", _("Testmail van {naam}").format(naam=ctx.company["name"]),
                             _("Deze testmail laat zien dat de e-mailinstellingen werken."))
        how = mailer.send(ctx.settings, ms, mailer.build_message(ms, out), out.recipients)
    except MailError as exc:
        ctx.log("mail_test_failed", error=str(exc)[:300])
        return _settings_page(ctx, ms, 400, lang=lang, error=exc.text())
    ctx.log("mail_test_sent", to=to, via=how)
    return _settings_page(ctx, ms, lang=lang, notice=_("Testmail verstuurd naar {adres}.").format(adres=to)
                          if ms.mode == "smtp" else _("Testmail in de map gezet (testmodus)."))


# ================================================================== mailscherm per document

def _load_document(ctx: Ctx, key: str, trans_id: int, variant: Optional[str]):
    require_role(ctx, "bookkeeper")
    conn, lib, loc, v = load(ctx, key, trans_id, variant or None)
    try:
        conn.commit()           # geen open transactie terwijl de mail verstuurd wordt
    except BaseException:
        conn.close()
        raise
    return conn, lib, loc, v


def mail_vars(loc: doctypes.Located, v: dict) -> dict:
    """Plaatshouders {nummer}, {datum}, {vervaldatum}, {bedrag} ook voor orders en offertes."""
    if loc.source != "order":
        return v
    quo = loc.order_type in ("sales_quotation", "request_quotation")
    return {**v, "invnumber": v.get("quonumber" if quo else "ordnumber"),
            "invdate": v.get("quodate" if quo else "orddate"), "duedate": v.get("reqdate"),
            "invtotal": v.get("ordtotal")}


def _signature(ctx: Ctx) -> str:
    sig = (ctx.user.get("mail_signature") or "").strip()
    return f"\n\n-- \n{sig}" if sig else ""


def _mail_page(ctx: Ctx, loc: doctypes.Located, v: dict, ms: MailSettings, fields: dict, status: int = 200, **kw):
    return render(ctx, "mail/send.html", status, formname=loc.kind.key, trans_id=loc.trans_id, v=v, ms=ms, f=fields,
                  back=f"{loc.screen_url}/{loc.trans_id}", doc_label=_(loc.kind.label), number=loc.number,
                  filename=filename(loc), **kw)


@router.get("/document/{key}/{trans_id}/mail", name="document_mail")
def mail_form(key: str, trans_id: int, ctx: Ctx = Depends(ctx_company)):
    variant = ctx.request.query_params.get("variant") or ""
    conn, lib, loc, v = _load_document(ctx, key, trans_id, variant)
    conn.close()
    ms = mailer.load(ctx.conn, ctx.company["id"])
    lang = renderer(ctx).language(str(v.get("language_code") or ""))
    to = v.get("email") or ""
    mv = mail_vars(loc, v)
    fields = {"to": to, "cc": v.get("cc") or "", "bcc": v.get("bcc") or "", "variant": variant,
              "subject": mailer.fill(ms.text(lang, key, "subject"), mv),
              "message": mailer.fill(ms.text(lang, key, "body"), mv) + _signature(ctx)}
    warning = None if to else _("Deze relatie heeft geen e-mailadres; vul het hieronder in.")
    return _mail_page(ctx, loc, v, ms, fields, warning=warning)


@router.post("/document/{key}/{trans_id}/mail")
def mail_send(key: str, trans_id: int, ctx: Ctx = Depends(ctx_company), form: FormData = Depends(csrf_form)):
    variant = str(form.get("variant", "") or "")
    conn, lib, loc, v = _load_document(ctx, key, trans_id, variant)
    ms = mailer.load(ctx.conn, ctx.company["id"])
    fields = {k: str(form.get(k, "") or "") for k in ("to", "cc", "bcc", "subject", "message")}
    fields["variant"] = variant
    with conn:
        try:
            out = mailer.compose(fields["to"], fields["cc"], fields["bcc"], fields["subject"], fields["message"])
            pdf = renderer(ctx, conn).render_pdf(key, v)
            out.attachments.append((filename(loc), pdf, "application/pdf"))
            msg = mailer.build_message(ms, out, reply_to=ctx.user.get("email") or "")
            how = mailer.send(ctx.settings, ms, msg, out.recipients)
        except MailError as exc:
            ctx.log("document_email_failed", formname=key, trans_id=trans_id, error=str(exc)[:300])
            return _mail_page(ctx, loc, v, ms, fields, 400, error=exc.text())
        # zoals io.pl print_form: status, notitie [email], audittrail
        mark_status(conn, lib.cfg, trans_id, loc.kind.formname, emailed=True, table=loc.table, reference=loc.number)
        add_intnote(conn, trans_id, _intnote(ctx, out), table=loc.table)
    ctx.log("document_emailed", formname=key, trans_id=trans_id, reference=loc.number,
            to=[a for __, a in out.to], cc=[a for __, a in out.cc], bcc=[a for __, a in out.bcc], via=how)
    raise Redirect(f"{loc.screen_url}/{trans_id}?ok=document_emailed")


def _intnote(ctx: Ctx, out: mailer.Outgoing) -> str:
    """Notitie zoals io.pl (r1873–1880): [email], datum, aan, cc, bcc, onderwerp, bericht."""
    now = datetime.now()
    lines = ["[email]", f"{_('Datum')}: {format_date(now.date(), ctx.user['dateformat'])} {now:%H:%M}",
             f"{_('Aan')}: {', '.join(a for __, a in out.to)}"]
    if out.cc:
        lines.append(f"{_('Cc')}: {', '.join(a for __, a in out.cc)}")
    if out.bcc:
        lines.append(f"{_('Bcc')}: {', '.join(a for __, a in out.bcc)}")
    lines.append(f"{_('Onderwerp')}: {out.subject}")
    message = out.body.split("\n-- \n", 1)[0].strip()
    lines += ["", f"{_('Bericht')}: {message or _('verzonden')}"]
    return "\n".join(lines)
