# PyBalans mit einer Run-my-Accounts-Sicherung testen

*English: [`TESTING-RMA.md`](TESTING-RMA.md)*

Dies ist eine private Testversion von PyBalans. Sie liest eine Sicherung von **Run my Accounts** (oder ledger123) in
einen neuen Mandanten ein, prüft, dass die Zahlen unverändert sind, und zeigt einen Bericht darüber, was umgesetzt und
was aufbewahrt wurde.

**Ihre Daten bleiben bei Ihnen.** Alles läuft auf Ihrem eigenen Computer in Docker. PyBalans spricht nur mit der
eigenen Datenbank; E-Mails verschickt es nur, wenn Sie darin einen Mailserver einrichten. Ihre Run-my-Accounts-
Installation und die Sicherungsdatei werden nicht verändert. Bitte schicken Sie uns **nicht** die Sicherung, sondern
nur den Bericht (siehe Schritt 6).

## Was Sie brauchen

- Docker: [Docker Desktop](https://www.docker.com/products/docker-desktop/) auf Mac oder Windows, oder Docker Engine
  mit dem Compose-Plugin unter Linux. Etwa 2 GB freier Speicherplatz.
- `git` und Zugang zum privaten Repository (Sie haben eine Einladung erhalten).
- Eine Sicherung eines Run-my-Accounts-Mandanten (siehe Schritt 4). Bitte nennen Sie uns vorher die Grösse: die
  Grenze liegt bei 512 MB.

## 1. Code holen

```
git clone https://github.com/<repository>.git pybalans
cd pybalans
```

## 2. PyBalans starten

Wählen Sie ein Passwort für die Datenbank (es verlässt Ihren Computer nie) und starten Sie:

```
echo 'ein-langes-passwort-waehlen' > db_password.txt
docker compose up -d --build
```

Beim ersten Mal dauert das einige Minuten. Zeigen Sie dann den Startcode an:

```
docker compose logs app
```

Suchen Sie nach `Start code / startcode: XXXX-XXXX-XXXX`.

Ist Port 8000 auf Ihrem Computer schon belegt, ändern Sie in `compose.yaml` `127.0.0.1:8000:8000` zum Beispiel in
`127.0.0.1:8010:8000` (und `http://localhost:8000` ein paar Zeilen darüber), führen Sie `docker compose up -d`
erneut aus und verwenden Sie unten diesen Port.

## 3. Erster Start

Öffnen Sie **http://localhost:8000/welkom** im Browser.

1. Wählen Sie Ihre Sprache (Deutsch, Englisch, Französisch oder Niederländisch).
2. Geben Sie den Startcode ein und legen Sie den Administrator an (Login, Name, Passwort).
3. Richten Sie die Zwei-Faktor-Authentifizierung mit einer Authenticator-App ein (1Password, Google oder Microsoft
   Authenticator). Für Administratoren ist das Pflicht.
4. Wählen Sie **Sicherung einlesen**.

## 4. Die Run-my-Accounts-Sicherung erstellen

Jede dieser Varianten funktioniert:

- die Sicherung aus den Run-my-Accounts-Masken (`.sql.gz`);
- die Sicherung von mojo-admin (`.sql.bz2`);
- ein einfacher `pg_dump -C <datenbank> | gzip > mandant.sql.gz`, erstellt auf dem Datenbankserver.

## 5. Einlesen

1. Wählen Sie die Datei und klicken Sie auf **Hochladen**. Die Prüfmaske sollte *Sicherung von Run my Accounts /
   ledger123* anzeigen, mit der Version (zum Beispiel 2.8.52).
2. Prüfen Sie den Namen und klicken Sie dann auf **Einlesen**. Je nach Grösse dauert das einige Sekunden bis mehrere
   Minuten; Sie können das Fenster schliessen, das Einlesen läuft auf dem Server weiter. Lang genug für eine Fika.

Was passiert:

- Alles, was das originale SQL-Ledger nicht kennt (die Bankschicht, Audit-Logs, MWST-Abrechnungen, Umlagerungen,
  Montagebelege, zusätzliche Spalten), wird unverändert in einem eigenen Teil der Datenbank aufbewahrt (Schema `rma`).
- Danach wird der Mandant auf die Struktur von SQL-Ledger 3.2 aktualisiert, die PyBalans verwendet.
- PyBalans vergleicht die **Summen- und Saldenliste pro Konto** und den **offenen Betrag pro Rechnung** vorher und
  nachher. Weicht etwas ab, wird nichts angelegt und die Abweichungen werden angezeigt.

## 6. Was wir gerne von Ihnen hören möchten

- Der Bericht auf der Ergebnisseite (später auch im Menü des Mandanten unter *Verwaltung > Run-my-Accounts-Daten*):
  ein Bildschirmfoto der Prüfung und der Abschnitte *In PyBalans übernommen* und *Aufbewahrt, aber (noch) ohne Platz
  in PyBalans*. Diese enthalten Anzahlen, keine Beträge pro Kunde.
- Wenn das Einlesen fehlschlägt: die Fehlermeldung und bei einer fehlgeschlagenen Prüfung die Liste der abweichenden
  Konten oder Belege (ein Bildschirmfoto genügt; dort stehen Beträge pro Konto, lassen Sie diese gerne weg).
- Alles, was Ihnen beim Durchsehen des Mandanten falsch vorkommt: Dashboard, Summen- und Saldenliste, offene Posten,
  Rechnungen.

## Bekannte Grenzen dieser Version

- **Bestand pro Lager** ist noch nicht umgesetzt: Der Bestand pro Artikel stimmt, steht in PyBalans aber unter
  „kein Lager“. Die Lagerzeilen von Run my Accounts bleiben aufbewahrt.
- Einige Run-my-Accounts-Daten haben in PyBalans noch keinen Platz (Mahnsperre, QR-IBAN, festes Erlöskonto pro
  Kunde, Skonto, die Bankschicht). Sie werden aufbewahrt und im Bericht gezählt.
- PyBalans hat einen Wechselkurs pro Währung und Tag; wo Run my Accounts unterschiedliche Kauf- und Verkaufskurse
  hatte, wird der Kaufkurs verwendet (jeder Beleg behält seinen eigenen Kurs).
- SQL-Ledger 3.2 hat ein Konto für Kursgewinne und -verluste; hatte Run my Accounts zwei, sagt der Bericht das.
- Es gibt keinen Weg zurück von PyBalans zu Run my Accounts. Ihre Originaldaten bleiben unberührt.

## Beenden oder entfernen

```
docker compose down        # beenden; Ihre Daten bleiben erhalten
docker compose up -d       # wieder starten
docker compose down -v     # beenden und ALLES LÖSCHEN (Datenbank und Dateien)
```

---

PyBalans ist von Matthijs Havinga. Fragen und Befunde: bitte als Issue in diesem Repository. Merci fürs Testen!
