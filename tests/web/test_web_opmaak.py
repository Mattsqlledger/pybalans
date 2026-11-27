"""v1.0.1 opmaak (PB18): technische controle op alle GET-pagina's zonder parameters (zoals de renderproef in
test_i18n): geen inline style met een vaste breedte, en elk veld in een formulierraster (.grid, .glhead, .fgrid)
heeft de vaste vorm bijschrift – veld – hulptekst, zodat het raster (subgrid) bijschriften en velden op één lijn zet.
"""

import re
from html.parser import HTMLParser

from webhelpers import login, make_user

from test_i18n import _get_pages

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
GRID = {"grid", "glhead", "fgrid"}
#: wat op de plaats van "het veld" mag staan
CONTROLS = {"input", "select", "textarea"}
CONTROL_CLASSES = {"pair", "datefield", "filepick", "colorpick", "inline-fields"}


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())


class Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("root", [], None)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if data.strip():
            self.cur.children.append(data)


def _items(label: Node):
    """De rasteronderdelen van een label: tekst en zichtbare elementen (verborgen velden, datalists en scripts
    tellen niet mee)."""
    out = []
    for c in label.children:
        if isinstance(c, str):
            if out and out[-1] == "#text":
                continue
            out.append("#text")
        elif c.tag in ("script", "datalist", "template") or "hidden" in c.attrs \
                or (c.tag == "input" and c.attrs.get("type") == "hidden"):
            continue
        else:
            out.append(c)
    return out


def label_problems(html: str):
    tree = Tree()
    tree.feed(html)
    problems, todo = [], [tree.root]
    while todo:
        n = todo.pop()
        todo.extend(c for c in n.children if isinstance(c, Node))
        if not (n.classes & GRID):
            continue
        for lab in n.children:
            if not isinstance(lab, Node) or lab.tag != "label" or "check" in lab.classes:
                continue
            items = _items(lab)
            ok = 2 <= len(items) <= 3 and items[0] == "#text" and isinstance(items[1], Node) \
                and (items[1].tag in CONTROLS or items[1].classes & CONTROL_CLASSES)
            if ok and len(items) == 3:
                ok = isinstance(items[2], Node) and items[2].tag in ("span", "small", "p", "div")
            if not ok:
                text = " ".join(c for c in lab.children if isinstance(c, str)).strip()[:40]
                problems.append(f"{text!r}: " + " | ".join(i if isinstance(i, str) else i.tag for i in items))
    return problems


WIDTH_STYLE = re.compile(r'style="[^"]*\b(?:min-|max-)?width\s*:', re.I)


def test_form_grid_structure_and_no_fixed_widths(client, db, company):
    secret = make_user(db, "admin", sysadmin=True, totp=True, memberships=[(company["id"], "admin", "jan")])
    login(client, "admin", secret)
    page = client.get("/administratie").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    client.post("/administratie", data={"csrf": csrf, "company_id": company["id"]})
    pages = _get_pages(client.app)
    assert len(pages) > 40
    problems, checked = [], 0
    for path in pages:
        r = client.get(path)
        if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", ""):
            continue
        checked += 1
        if WIDTH_STYLE.search(r.text):
            problems.append(f"{path}: inline style met breedte")
        problems += [f"{path}: {p}" for p in label_problems(r.text)]
    assert checked > 40
    assert not problems, problems


def test_label_check_itself():
    good = '<div class="grid"><label>Naam <input name="n"><span class="muted small">uitleg</span></label>' \
           '<label class="check"><input type="checkbox"> Vinkje</label></div>'
    bad = '<div class="grid"><label><input name="n"> Naam</label>' \
          '<label>Bedrag <input name="a"> EUR <span>x</span></label></div>'
    assert label_problems(good) == []
    assert len(label_problems(bad)) == 2
