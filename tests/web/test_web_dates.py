"""Datumvelden (v1.0.1 PB19): in filters betekent een leeg datumveld "geen grens"; de server mag er niet over
vallen en niet zelf een grens kiezen (behalve waar dat zo bedoeld is, zie README keuzes PB19)."""

import pytest

from webhelpers import csrf, login, make_user


@pytest.fixture
def anna(client, db, company):
    """Systeembeheerder en beheerder van de administratie."""
    secret = make_user(db, "anna", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "anna")])
    login(client, "anna", secret)
    page = client.get("/administratie").text
    client.post("/administratie", data={"csrf": csrf(client, page), "company_id": company["id"]})
    return client


@pytest.mark.parametrize("url, params", [
    ("/systeem/logboek", {"from": "", "to": ""}),
    ("/beheer/audittrail", {"van": "", "tot": ""}),
    ("/beheer/koersen", {"valuta": "", "van": "", "tot": ""}),
    ("/bijlagen", {"datefrom": "", "dateto": ""}),
    ("/documenten/batch", {"soort": "invoice", "van": "", "tot": "", "staat": "alle"}),
    ("/betalingsregister", {"van": "", "tot": ""}),
    ("/verkooporders", {"datefrom": "", "dateto": ""}),
    ("/artikelen", {"datefrom": "", "dateto": ""}),
    ("/klanten", {"startdatefrom": "", "startdateto": ""}),
    ("/artikelen-productie", {"datefrom": "", "dateto": ""}),
    ("/uren/tijdkaarten", {"van": "", "tot": ""}),
    ("/rapporten/journaal", {"run": "1", "period": "", "fromdate": "", "todate": ""}),
    ("/rapporten/winst-en-verlies", {"run": "1", "period": "", "fromdate": "", "todate": ""}),
])
def test_empty_date_filters(anna, url, params):
    r = anna.get(url, params=params)
    assert r.status_code == 200, (url, r.text[:500])
    assert "Ongeldige datum" not in r.text


def test_date_fields_have_no_server_default_when_cleared(anna):
    """Een leeggemaakt filter blijft leeg (geen standaardperiode terug), ook na het versturen."""
    page = anna.get("/documenten/batch", params={"soort": "invoice", "van": "", "tot": ""}).text
    assert 'name="van" value=""' in page and 'name="tot" value=""' in page
    page = anna.get("/betalingsregister", params={"van": "", "tot": ""}).text
    assert 'name="van" value=""' in page
