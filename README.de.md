# fds-mcp

*[English version](README.md) — die englische Fassung ist die Quelle, diese hier folgt
ihr.*

Ein [MCP](https://modelcontextprotocol.io)-Server für die API von
[FragDenStaat.de](https://fragdenstaat.de), der deutschen Informationsfreiheits-Plattform
auf Basis von [froide](https://github.com/okfde/froide).

Er gibt einer KI-Assistenz die *Recherche- und Vorbereitungsseite* einer IFG-Anfrage: die
zuständige Behörde finden und belegen, warum sie zuständig ist, das anwendbare Gesetz und
seine Frist lesen, eigene Anfragen verfolgen, Antworten und Anhänge einsammeln und einen
neuen Antrag als lokale Datei entwerfen, die Sie prüfen, bevor irgendetwas Ihren Rechner
verlässt.

Absenden ist möglich, aber absichtlich das Schwerste, was dieser Server tut.

---

## ⚠️ `POST /api/v1/request/` sendet sofort und unwiderruflich

Die REST-API von FragDenStaat hat **keinen Entwurfsmodus, keine Vorschau und kein
Rückgängig**. In dem Moment, in dem ein `POST /api/v1/request/` gelingt, ist die E-Mail
zur Behörde unterwegs, die Anfrage ist (standardmäßig) öffentlich unter CC0, und sie ist
nicht zurückholbar.

Schlimmer: `MakeRequestSerializer` hat **kein Feld `law_type`**. Die API legt deshalb
immer unter `publicbody.default_law` ab, und weil froide nach `("-meta", "-priority")`
sortiert, ist das fast immer das *kombinierte Meta-Gesetz* — nicht das Gesetz, das Sie
meinten. Für eine rheinland-pfälzische Kommune landet ein Antrag nach dem LTranspG
(Gesetz 16) über die API stillschweigend unter „LTranspG, VIG" (Gesetz 18).

**Deshalb ist der empfohlene Ausgang dieses Servers `build_submit_url`**: Sie bekommen ein
vorausgefülltes Webformular mit dem richtigen `law_type` und drücken selbst auf Senden.
`submit_request` existiert, hat `dry_run=True` als Standard und verweigert, solange nicht
alle fünf unabhängigen Gates zustimmen — siehe [Sicherheitsmodell](#sicherheitsmodell).

---

## Dokumentation

- [OAuth-Einrichtung, mit Screenshots](docs/oauth-setup.de.md) — Anwendung registrieren,
  Scopes, die Regeln für die Redirect-URI und was zu tun ist, wenn der lokale
  HTTPS-Listener blockiert wird
- [README.md](README.md) und [docs/oauth-setup.md](docs/oauth-setup.md) — dieselben
  Dokumente auf US-Englisch. Englisch ist der Standard, Deutsch läuft daneben.

## Installation

```bash
pip install git+https://github.com/dwolbeck/fds-mcp.git
```

Oder aus einem Checkout:

```bash
git clone https://github.com/dwolbeck/fds-mcp.git
cd fds-mcp
pip install -e ".[dev]"
```

Benötigt Python 3.10 oder neuer.

### Beim MCP-Client anmelden

Der Server spricht stdio. Für Claude Code:

```bash
claude mcp add fds -- fds-mcp serve
```

Für einen Client mit JSON-Konfiguration:

```json
{
  "mcpServers": {
    "fds": {
      "command": "fds-mcp",
      "args": ["serve"]
    }
  }
}
```

Die vier grünen Werkzeuge funktionieren sofort, ohne Konto und ohne Token.

---

## OAuth einrichten

Die gelben und roten Werkzeuge brauchen ein OAuth-2.0-Bearer-Token. FragDenStaat
unterstützt für seine API genau zwei Authentifizierungsverfahren — OAuth2 und
Session-Cookies. Es gibt **keinen persönlichen API-Key und kein Basic Auth** (froides
eigene `docs/api.rst` behauptet etwas anderes; sie ist veraltet).

Die ausführliche Anleitung steht in [docs/oauth-setup.de.md](docs/oauth-setup.de.md).

### 1. Anwendung registrieren

Melden Sie sich an und öffnen Sie
<https://fragdenstaat.de/account/applications/register/>. Alle Kontoseiten sind durch
`recent_auth_required` geschützt; Sie werden also möglicherweise erneut nach Ihrem
Passwort gefragt.

| Feld | Wert |
| --- | --- |
| Name | beliebig, z. B. `fds-mcp` |
| Client-Typ | `public` (damit ist PKCE Pflicht) |
| Grant-Typ | `authorization-code` |
| Redirect-URI | `https://localhost:8765/callback` |

**Akzeptiert werden nur die Schemata `https` und `fragdenstaat`.**
`http://localhost/...` wird schon bei der Registrierung abgelehnt — das ist
`OAUTH2_PROVIDER.ALLOWED_REDIRECT_URI_SCHEMES` auf dem Server. Deshalb ist der
Standard-Redirect ein HTTPS-Loopback-Listener mit einem selbstsignierten Zertifikat, das
`fds-mcp` (über `openssl`) für Sie erzeugt, und deshalb ist der Rückfallweg
`fragdenstaat://callback` mit manuellem Einfügen.

### 2. Konfigurieren und anmelden

```bash
fds-mcp configure --client-id <ihre-client-id>
fds-mcp login
```

`login` führt Authorization Code + PKCE (S256) aus, öffnet den Browser und fängt den
Redirect auf `https://localhost:8765/callback` ab. Ihr Browser warnt vor dem
selbstsignierten Zertifikat — das ist der lokale Listener; akzeptieren Sie es.

Ohne Browser oder ohne `openssl`:

```bash
fds-mcp login --manual     # nutzt fragdenstaat://callback, Sie fügen die URL zurück ein
```

Die Tokens landen in `~/.config/fds-mcp/tokens.json` mit Modus `0600`. Der Refresh läuft
automatisch; Refresh-Tokens sind **180 Tage** gültig.

```bash
fds-mcp status     # Konfiguration, Token- und Throttle-Zustand
fds-mcp whoami     # das angemeldete Konto
fds-mcp logout --revoke
```

### Scopes

`fds-mcp` fordert standardmäßig `read:user read:request make:request` an. Mit
`fds-mcp configure --scopes "read:user read:request"` überschreiben Sie das, wenn Sie nie
absenden wollen.

| Scope | Gebraucht für |
| --- | --- |
| `read:user` | das eigene Konto identifizieren (`/api/v1/user/`) |
| `read:request` | eigene, auch nicht-öffentliche Anfragen |
| `make:request` | `POST /api/v1/request/` — absenden |
| `write:message`, `write:attachment` | Postbriefe dokumentieren (noch nicht umgesetzt) |

Nichts davon erlaubt Löschen. Wenn Sie `make:request` weglassen, kann `submit_request`
niemals funktionieren, und alles andere funktioniert weiter.

---

## Werkzeuge

| Werkzeug | Stufe | Auth | Nebenwirkung |
| --- | --- | --- | --- |
| `search_authorities(query, jurisdiction=None, limit=20)` | 🟢 grün | keine | keine |
| `get_authority(id)` | 🟢 grün | keine | keine |
| `get_law(id)` | 🟢 grün | keine | keine |
| `check_jurisdiction(place_name)` | 🟢 grün | keine | keine |
| `list_my_requests(status=None, limit=50)` | 🟡 gelb | Token, nur lesend | keine |
| `get_request(id)` | 🟡 gelb | Token, nur lesend | keine |
| `get_messages(request_id)` | 🟡 gelb | Token, nur lesend | keine |
| `list_attachments(message_id)` | 🟡 gelb | Token, nur lesend | keine |
| `download_attachment(attachment_id, target_dir)` | 🟡 gelb | Token, nur lesend | schreibt eine lokale Datei |
| `check_deadlines()` | 🟡 gelb | Token, nur lesend | keine |
| `build_reply_draft(request_id, text, subject=None, path=None)` | 🟡 gelb | Token, nur lesend | schreibt eine lokale YAML-Datei, wenn `path` gesetzt ist |
| `create_request_draft(...)` | 🔴 rot | keine | schreibt eine lokale YAML-Datei, **überhaupt kein Netz** |
| `validate_draft(path)` | 🔴 rot | keine | liest die API für L01–L06 |
| `build_submit_url(path)` | 🔴 rot | keine | schreibt eine lokale `.body.txt`-Datei |
| `submit_request(path, confirmation_token)` | 🔴 rot | Token + `make:request` | **sendet die Anfrage, unwiderruflich** |
| `send_reply_via_browser(draft_path, confirmation_token)` | 🔴 rot, **opt-in** | ein angemeldetes Browser-Profil | **sendet die Antwort, unwiderruflich** |

Alle vier roten Werkzeuge haben `dry_run: bool = True`.

`send_reply_via_browser` ist das sechzehnte Werkzeug und wird **nicht registriert**,
solange nicht `FDS_MCP_BROWSER_SEND=1` gesetzt ist. Ohne diese Variable taucht es in der
Werkzeugliste gar nicht auf. Lesen Sie [Antworten senden](#antworten-senden), bevor Sie es
einschalten.

### `check_jurisdiction` liefert seinen Beleg mit

Es folgt `/georegion/?name=<Ort>` die `part_of`-Kette nach oben und fragt auf jeder Ebene
`/publicbody/?regions=<id>`; zurück kommen die Regionenkette, die passenden Behörden und
die Liste der benutzten API-URLs. Das zählt in Rheinland-Pfalz, wo eine Ortsgemeinde oft
gar nicht bei FragDenStaat gelistet ist, die verwaltende Verbandsgemeindeverwaltung aber
schon.

### `build_submit_url` ist bei langen Anträgen zweistufig

Gemessen am 2026-09-05: fragdenstaat.de beantwortet GET-URLs ab ungefähr **4096 Byte** mit
HTTP 400 (4086 Byte → 200, 4106 Byte → 400). Ein typischer 4000-Zeichen-Antrag
überschreitet das nach URL-Kodierung. Oberhalb der Grenze liefert das Werkzeug eine kurze
URL, die Betreff und `law_type` vorbefüllt, plus den Text in einer `.body.txt`-Datei neben
dem Entwurf, die Sie ins Formular einfügen.

### Was die API von FragDenStaat nicht kann

Das Folgende gibt es nur im Frontend, ohne REST-Entsprechung. Der Server tut nicht so, als
wäre es anders:

- **einer Behörde antworten** — `POST /api/v1/message/` legt nur *Post*-Nachrichten an
  (`OnlyPostalMessagesWritable`), und `subject`/`content` sind im Serializer nur lesbar.
  E-Mail-Antworten laufen über `/anfrage/<slug>/send/message/`, eine CSRF-geschützte
  Django-View, die Bearer-Tokens ignoriert. Siehe [Antworten senden](#antworten-senden);
- **die Rechtsgrundlage wählen** — kein `law_type` im Serializer;
- **Entwürfe** — `RequestDraft` ist nicht im API-Router registriert;
- Status, Ergebnis, Tags oder Gesetz nachträglich setzen; eine Anfrage veröffentlichen;
  Widerspruch einlegen oder den Landesbeauftragten anrufen.

---

## Antworten senden

**Eine E-Mail-Antwort an eine Behörde lässt sich über die API von FragDenStaat nicht
senden.** Nicht mit anderem Payload, nicht mit einem zusätzlichen Scope, nicht mit einem
besseren Token. Drei Messungen vom 2026-09-05, festgehalten in
`tests/test_api_contract.py`:

1. `POST /api/v1/message/` mit `kind: "email"` antwortet mit **HTTP 400** und meldet unter
   dem Schlüssel `kind`: *„Nachrichten dieser Art können nicht über die API erstellt
   werden."* Das ist froides `OnlyPostalMessagesWritable`.
2. Derselbe Aufruf mit `kind: "post"` antwortet ebenfalls mit 400 — die Sonde trägt
   absichtlich eine nicht auflösbare Request-URI, es kann so oder so nichts entstehen —,
   aber **ohne `kind`-Fehler**. Das ist die Kalibrierung. Ohne sie würde die erste Messung
   nichts beweisen: eine 400 könnte genauso gut von der ungültigen URI kommen, davon, dass
   der Endpunkt jedes POST ablehnt, oder von einem fehlenden Scope.
3. `POST https://fragdenstaat.de/anfrage/<slug>/send/message/` antwortet mit **HTTP 302
   auf `/account/login/`** — mit und ohne Bearer-Token identisch, gleicher Status,
   gleiches `Location`. Die Web-View ist Session + CSRF, mehr nicht. OAuth ist kein Weg an
   Punkt 1 vorbei.

Die ehrliche Antwort lautet also: ein Mensch sendet die Antwort. `build_reply_draft` macht
das kurz.

### `build_reply_draft`

Holt die Anfrage, prüft Ihren Text und gibt die fertige Nachricht zurück, dazu einen
Betreff im Format von froide selbst (`AW: <Titel> [#<id>]`) und die URL des Formulars. Es
schreibt nichts ins Netz — es gibt kein Argument, mit dem es sendet.

Eine Folgenachricht wird **anders geprüft als ein Antrag**, und der Unterschied ist leicht
zu übersehen. froide rahmt einen neuen Antrag mit `letter_start`/`letter_end` des
Gesetzes; eine Folgenachricht rahmt es überhaupt nicht. Das Textfeld kommt vorbefüllt mit

```
Guten Tag,

…

Mit freundlichen Grüßen
<Ihr Name>
```

und genau das, was darin steht, bekommt die Behörde. Deshalb:

- `R19` **verlangt** Anrede und Grußformel, je genau einmal — die Umkehrung von `R10`, das
  beides verbietet, solange der Rahmen greift;
- `R04` lehnt den Platzhalter `…` (U+2026) ab, der in diesem Formular gerade steht. Das
  ist auf diesem Weg der wahrscheinlichste Fehler;
- `R06` hält E-Mail-Adressen und IBANs aus einem Verlauf heraus, der öffentlich und CC0
  ist;
- der Betreff ist auf 230 Zeichen begrenzt.

Jedes Ergebnis trägt eine weitere Warnung, ausnahmslos: **das Formular führt Ihre
Postadresse vorbefüllt mit**, hinter einer Checkbox „Adresse mitsenden". Bei einer
öffentlichen Anfrage veröffentlicht ein Haken dort, wo Sie wohnen — unter CC0 und
dauerhaft. Lassen Sie den Haken aus, sofern die Behörde nicht ausdrücklich nach Ihrer
Postanschrift gefragt hat.

### `send_reply_via_browser` — optional, standardmäßig aus

Man kann den letzten Schritt trotzdem automatisieren: das Formular in einem Browser
bedienen, der Ihre angemeldete Sitzung trägt. Dieser Server kann das, und es ist **nicht
eingeschaltet**. Registriert wird es nur, wenn `FDS_MCP_BROWSER_SEND=1` gesetzt ist, und
es braucht ein zusätzliches Paket:

```bash
pip install 'fds-mcp[browser]'
python -m playwright install chromium
export FDS_MCP_BROWSER_SEND=1
```

Es formuliert nie Text. Es sendet `subject` und `body` einer Reply-Draft-Datei, die
`build_reply_draft` geschrieben und ein Mensch danach freigegeben hat — eine andere
Eingabe nimmt es nicht. Fünf Gates:

1. die Datei ist ein Reply-Draft mit `status: approved`, und `send_address` ist falsch;
2. kein offener `ERROR`-Befund unter den Folgenachrichten-Regeln;
3. `confirmation_token` stimmt Byte für Byte mit dem Wert überein, den ein Mensch in die
   Datei geschrieben hat;
4. die lokale Buchführung sagt, dass eine weitere Nachricht innerhalb von `2/5min`,
   `6/6h`, `8/24h` bleibt. froide erzwingt `message_throttle` auf diesem Weg nicht in
   einer Form, auf die wir uns verlassen können — diese Bremse ist freiwillig;
5. im Formular selbst: „Adresse mitsenden" ist aus, der Empfänger ist lesbar und wird
   berichtet, Betreff und Nachricht werden nach dem Eintippen Byte für Byte
   zurückgelesen, kein U+2026, und je genau eine Anrede und eine Grußformel. Was es nicht
   findet, wertet es als Fehlschlag — ein Formular, das seine Gestalt geändert hat, ist
   ein Formular, in dem es keine Knöpfe drücken darf.

Danach fragt es die API, ob auf der Anfrage tatsächlich eine neue Nachricht existiert.
Wenn nicht, gilt der Versand als `unconfirmed`, und der Entwurf wird **nicht** als
gesendet markiert. Ungeklärt ist weder Fehlschlag noch Erfolg.

> [!WARNING]
> **Was Sie in Kauf nehmen, wenn Sie das einschalten**
>
> 1. **Browser-Automatisierung umgeht das Prinzip, dass ein Mensch die letzte Handlung
>    ausführt.** Jeder andere Ausgang dieses Servers endet damit, dass eine Person auf
>    Senden klickt. Dieser nicht.
>
> 2. **Neben einem generischen Schreibwerkzeug sind Gate 1 und Gate 3 keine Gates.** Es
>    sind zwei Werte in einer YAML-Datei auf Ihrer Platte. Kein Werkzeug in *diesem*
>    Server kann eines davon setzen — `build_reply_draft` schreibt immer `status: draft`
>    und den Platzhalter-Token. Aber die meisten MCP-Hosts geben dem Modell zusätzlich ein
>    `write_file`, und ein Modell, das Dateien schreiben kann, kann `status: approved` und
>    einen selbst gewählten Token schreiben. Zusammen mit einer Prompt-Injection aus der
>    Antwort einer Behörde — Text, den dieser Server liest und als nicht vertrauenswürdig
>    kennzeichnet, dem Modell aber trotzdem vorlegt — geht Behördenpost ohne menschliches
>    Zutun raus. Sie ist nicht zurückholbar.
>
> 3. **Der Browser trägt eine angemeldete Sitzung von Ihnen.** Eine Fehlfunktion wirkt mit
>    Ihren vollen Rechten auf fragdenstaat.de: Ihre Anfragen, Ihre Kontoseiten, Ihre
>    Adresse.
>
> 4. **Gegenmaßnahmen**, nach Wirksamkeit:
>    - das Feature aus lassen. Ohne `FDS_MCP_BROWSER_SEND` existiert das Werkzeug nicht.
>    - ein **separates Browser-Profil** ohne andere Logins benutzen, über
>      `FDS_MCP_BROWSER_PROFILE`. Die Sitzung in diesem Profil ist der Schadensradius.
>    - das Draft-Verzeichnis mit `FDS_MCP_DRAFT_DIR` **außer Reichweite Ihrer anderen
>      Werkzeuge** legen. Gate 1 und Gate 3 sind nur so lange etwas wert, wie nichts
>      anderes diese Datei schreiben kann.
>    - im Normalbetrieb `dry_run=True` lassen. Es füllt das Formular und hält vor dem
>      Klick an.

---

## Sicherheitsmodell

Sieben Regeln sind im Code durchgesetzt, nicht bloß dokumentiert. Zu jeder gibt es Tests
in `tests/test_security_gates.py`, die zeigen, dass sie greift.

1. `submit_request` bricht ab, solange der `status` des Entwurfs nicht `approved` ist —
   das setzt ein Mensch.
2. Es bricht ab, solange ein `ERROR`-Befund offen ist.
3. Es bricht ab, wenn `law.wunsch_id != law.api_default_id`, weil die API `law_type` nicht
   setzen kann und unter dem falschen Gesetz einreichen würde.
4. Es bricht ab, wenn `confirmation_token` nicht Byte für Byte dem Token entspricht, das
   ein Mensch in die Entwurfsdatei geschrieben hat. Ein Werkzeug darf diesen Token nicht
   erfinden.
5. Jedes rote Werkzeug hat `dry_run: bool = True` als Standard.
6. Eine lokale Buchführung prüft `5/5min`, `6/6h`, `10/24h`, `20/7d` vor jedem POST und
   **bricht mit klarer Meldung ab, statt es erneut zu versuchen**. Die Nutzungsbedingungen
   von FragDenStaat (B.1.4) sperren ein Konto für einen Monat schon für den *Versuch*, die
   Grenzen zu umgehen.
7. Der HTTP-Client verweigert jede Methode außer GET, solange nicht ausdrücklich
   `allow_write=True` gesetzt wurde. Genau eine Funktion im Paket setzt das je.

`send_reply_via_browser` hat seine eigene Kette aus fünf Gates, aufgeführt unter
[Antworten senden](#antworten-senden), mit Tests in `tests/test_browser_send.py`. Dazu
kommt ein Gate, das die anderen nicht brauchen: Regel 0, *das Werkzeug wird gar nicht erst
registriert*, solange nicht `FDS_MCP_BROWSER_SEND=1` gesetzt ist.

### Das Regelwerk

Die Offline-Regeln `R01`–`R19` bilden nach, was froides *Webformular* durchsetzt — und das
ist erheblich mehr, als die REST-API prüft. Die Live-Regeln `L01`–`L06` prüfen gegen die
API: die Behörde existiert und heißt noch so, das gewünschte Gesetz wird tatsächlich
angeboten, der neu berechnete API-Standard passt zu dem, was der Entwurf behauptet, es
gibt keine doppelte Anfrage, kein Satz Ihres Textes steht schon im Anschreiben des
Gesetzes, und das fertige Anschreiben enthält alle Bausteine, die es enthalten sollte.

Bemerkenswert:

- `R06` lehnt E-Mail-Adressen und IBANs im Text ab. Öffentliche Anfragen sind CC0 und für
  alle sichtbar — setzen Sie keine anderen Menschen hinein.
- `R10` wertet Anrede oder Grußformel bei `full_text=false` als **Fehler**: froide rahmt
  den Text selbst mit `letter_start`/`letter_end` des Gesetzes, wer beides selbst schreibt,
  verschickt eine doppelte Begrüßung.
- `R12` ist ein Fehler bei `submit_via: api` und nur ein Hinweis bei
  `submit_via: web_form` — das Webformular kann das Gesetz wählen, die API nicht.
- `R18` und `L06` prüfen **das Anschreiben, das die Behörde bekommt**, nicht den Text, den
  Sie geschrieben haben. Bei `full_text=false` steuern `letter_start`/`letter_end` des
  Gesetzes einen Teil bei, ein Baustein kann also von beiden Seiten kommen; `L06` holt den
  Rahmen und meldet, was keine der beiden Seiten liefert. Sechs Bausteine:
  Rechtsgrundlage (der einzige `ERROR`), Kosten-Vorabinformation, Kostendeckel, Frist,
  Weiterleitung bei Unzuständigkeit, elektronische Antwort.

  Der Kostendeckel ist der Grund, warum es das gibt. Eine echte Anfrage ging ohne einen
  solchen raus, weil das `letter_end` des LTranspG zwar um Vorabinformation über die
  Kosten bittet, aber weder einen Betragsdeckel nennt noch auf die gebührenfreie
  Einsichtnahme vor Ort zurückfällt. Alles andere deckte die Vorlage ab — genau deshalb
  fand ein Blick allein auf den Text nichts.
- `R19` ist die Umkehrung von `R10` und gilt nur für Folgenachrichten — siehe
  [Antworten senden](#antworten-senden).

### Wo der Server schreiben darf

Drei Werkzeug-Argumente sind Dateipfade, die das *Modell* wählt, und dasselbe Modell liest
Behördenantworten und Anhänge — von Dritten geschriebenen Text. Die Pfade werden deshalb
eingeschränkt statt vertraut:

- Entwurfspfade müssen auf `.yaml`/`.yml` enden, werden vor der Prüfung aufgelöst (ein
  Symlink wird an seinem Ziel gemessen), und `save()` weigert sich, eine Datei zu
  überschreiben, die selbst kein Entwurf ist;
- `download_attachment` legt kein Verzeichnis an, und der Dateiname des Anhangs wird auf
  seinen Basisnamen reduziert, alles außerhalb von `[A-Za-z0-9._ -]` wird ersetzt;
- Anhänge werden ausschließlich von `fragdenstaat.de` und `media.frag-den-staat.de`
  geholt, und das Bearer-Token geht nirgendwo anders hin.

Vier Umgebungsvariablen ziehen das enger und sind empfohlen, sobald der Server
unbeaufsichtigt läuft:

| Variable | Wirkung |
| --- | --- |
| `FDS_MCP_DRAFT_DIR` | jeder Entwurfspfad muss in diesem Verzeichnis bleiben (`:`-getrennte Liste) |
| `FDS_MCP_DOWNLOAD_DIR` | jedes Ziel von `download_attachment` muss in diesem Verzeichnis bleiben |
| `FDS_MCP_BROWSER_SEND` | `1` registriert `send_reply_via_browser`. Alles andere, auch nicht gesetzt, und das Werkzeug existiert nicht |
| `FDS_MCP_BROWSER_PROFILE` | Browser-Profilverzeichnis für dieses Werkzeug. Zeigen Sie auf ein Profil, das bei fragdenstaat.de angemeldet ist **und sonst nirgends** |

Ergebnisse, die Text von Dritten enthalten (`get_messages`, `get_request`,
`list_attachments`, `download_attachment`), benennen diese Felder unter dem Schlüssel
`untrusted_content`. Sie sind Daten. Sie wählen keine Dateipfade, keine URLs, keine
Werkzeugaufrufe und keine Bestätigungs-Tokens.

### Der Lebenszyklus eines Entwurfs

```
draft ──validate_draft──▶ validated ──ein Mensch bearbeitet die Datei──▶ approved ──submit_request──▶ submitted
```

Ein Reply-Draft hat seinen eigenen Zyklus, der in `sent` endet statt in `submitted`, und
nur `send_reply_via_browser` erreicht diesen Zustand — und auch das erst, nachdem die API
bestätigt hat, dass eine neue Nachricht existiert.

Nur ein Mensch setzt einen Entwurf auf `approved`, und nur durch Bearbeiten der
YAML-Datei.

**Kennen Sie die Grenze dieses Satzes.** Gate 1 (`status: approved`) und Gate 4
(`confirmation_token`) sind zwei Werte in einer Datei auf Ihrer Platte. Kein Werkzeug in
*diesem* Server kann eines davon setzen — `create_request_draft` schreibt immer
`status: draft` und den Platzhalter-Token, und es gibt kein Werkzeug, das einen Entwurf
befördert. Aber die meisten MCP-Hosts geben dem Modell zusätzlich ein generisches
Werkzeug zum Schreiben von Dateien, und ein Modell, das Dateien schreiben kann, kann
`status: approved` und einen selbst gewählten Token schreiben. Zusammen mit einer
Prompt-Injection aus der Antwort einer Behörde ist das ein Weg zu einem echten Versand.

Wenn Sie das also neben einem Dateiwerkzeug betreiben:

- halten Sie `submit_request` ganz heraus, indem Sie die Scopes ohne `make:request`
  konfigurieren — dann kann kein Token, das dieser Server hält, je eine Anfrage POSTen;
- oder setzen Sie `FDS_MCP_DRAFT_DIR` auf ein Verzeichnis, in das Ihre anderen Werkzeuge
  nicht schreiben;
- oder lassen Sie es beim empfohlenen Ausgang und benutzen `build_submit_url`, wo der
  Senden-Knopf in Ihrem Browser sitzt und nicht in einem Werkzeugaufruf;
- und lassen Sie `FDS_MCP_BROWSER_SEND` ungesetzt. Dieselbe Überlegung gilt für
  `send_reply_via_browser`, eine Stufe schärfer: dort gibt es keinen Scope, den Sie
  vorenthalten können, nur eine Browser-Sitzung, die Ihnen gehört.

Die Gates 2, 3, 5 und 7 hängen nicht an der Datei und halten unabhängig davon: das
Regelwerk läuft gegen Live-API-Daten, die Gesetzesprüfung vergleicht mit dem neu
berechneten API-Standard, die Throttle-Buchführung ist eigener Zustand, und der
HTTP-Client verweigert Nicht-GET überall außer in `submit_request`.

---

## Entwicklung

```bash
pip install -e ".[dev]"
python -m pytest -m "not live"     # Offline-Suite
python -m pytest -m live           # geht an fragdenstaat.de
```

Netzzugriff ist über `pytest-socket` standardmäßig blockiert; nur mit `live` markierte
Tests dürfen `fragdenstaat.de` erreichen.

Die Live-Suite besteht aus lesenden GETs, mit genau zwei Ausnahmen, beide in
`tests/test_api_contract.py`, und beide können nichts anlegen: die Message-POSTs tragen
eine nicht auflösbare Request-URI, der Webformular-POST trägt einen leeren Body. **Kein
Test öffnet einen Browser, und kein Test sendet eine Nachricht.** Die Tests, die ein Token
brauchen, überspringen sich selbst, statt zu scheitern, wenn
`~/.config/fds-mcp/tokens.json` fehlt.

Das CLI funktioniert auch ohne MCP-Client:

```bash
fds-mcp validate examples/request-draft.yaml
fds-mcp validate examples/request-draft.yaml --live
```

---

## Fakten, und woher sie kommen

Alles, was dieser Server über die API behauptet, wurde am 2026-09-05 gegen
`fragdenstaat.de` geprüft, gegen `okfde/froide@bc6c2fa` und
`okfde/fragdenstaat_de@88bfbba`. Die Quellenangaben stehen in den Docstrings, bis auf
Datei und Zeile. Wenn Sie eine Behauptung finden, die falsch oder veraltet ist, ist das
ein Bug — bitte melden Sie ihn.

Die API ist unter <https://fragdenstaat.de/api/> dokumentiert, mit einem
OpenAPI-3.0.3-Schema unter <https://fragdenstaat.de/api/v1/schema/> und einer Swagger-UI
unter <https://fragdenstaat.de/api/v1/schema/swagger-ui/>. (`/api/v1/docs/`, das froides
eigene Dokumentation erwähnt, liefert 404.)

---

## Bitte gehen Sie verantwortlich damit um

FragDenStaat wird von einem gemeinnützigen Verein betrieben und aus Spenden bezahlt. Jede
Anfrage kostet eine Behörde echte Arbeitszeit. Die Grenzen von 5 Anfragen pro 5 Minuten
und 20 pro Woche haben einen Grund. Dieser Server ist dafür gebaut, *bessere* Anfragen zu
stellen, nicht mehr davon.

Anfragen mit `public: true` — dem Standard — veröffentlichen den gesamten
E-Mail-Verkehr, alle freigegebenen Anhänge und alle hochgeladenen Dokumente weltweit unter
CC0.

---

## Haftungsausschluss

**Dieses Projekt steht in keiner Verbindung zur Open Knowledge Foundation Deutschland
e. V., zu FragDenStaat oder zum froide-Projekt und wird von ihnen weder unterstützt noch
gebilligt.** Es ist ein unabhängiger Client eines Drittanbieters, der mit einer
öffentlichen API spricht. Alle Marken gehören ihren jeweiligen Inhabern.

Dies ist keine Rechtsberatung.

## Lizenz

MIT — siehe [LICENSE](LICENSE). Copyright 2026 Dirk Wolbeck.
