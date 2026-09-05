# Was die FragDenStaat-API kann und was nicht

Erkenntnisse aus dem Bau eines Clients gegen die REST-API von FragDenStaat.de (froide).
Alles hier wurde entweder **live gegen die Seite gemessen** oder **im Quellcode gelesen** —
bei jedem Punkt steht, was davon zutrifft. Messungen sind datiert; Verhalten kann sich ändern.

Basis: `fragdenstaat.de` am **2026-09-05**, `okfde/froide@bc6c2fa` (2026-09-03),
`okfde/fragdenstaat_de@88bfbba` (2026-09-04).

English version: [FINDINGS.md](FINDINGS.md)

---

## Die kurze Antwort

Die API ist zum **Lesen und Recherchieren** hervorragend und beim **Schreiben** bewusst eng.
Behörden finden, Gesetze und Fristen lesen, eigene Anfragen verfolgen, Antworten und Anhänge
einsammeln — alles möglich. Eine neue Anfrage stellen: möglich. Der Behörde **antworten**:
nicht möglich. Die Rechtsgrundlage beim Stellen direkt wählen: nicht möglich — aber es gibt
einen Umweg, siehe Abschnitt 3.

Nichts davon ist eine Dokumentationslücke. Wir haben nachgesehen.

---

## 1. Der Behörde antworten geht nicht. Überhaupt nicht.

Das ist die Grenze, gegen die man zuerst läuft, deshalb steht sie vorn.

`POST /api/v1/message/` sieht aus, als könnte es das. Kann es nicht.

**Gemessen am 2026-09-05**, mit Positiv- und Negativkontrolle über denselben Pfad:

```
POST /api/v1/message/   kind="email"   → 400
  {"kind": ["Nachrichten dieser Art können nicht über die API erstellt werden."]}

POST /api/v1/message/   kind="post"    → 400
  (kein kind-Fehler — das ist die Kalibrierung; ohne sie beweist das erste Ergebnis nichts)
```

Beide Aufrufe nutzten eine absichtlich ungültige `request`-URI, damit der Serializer
scheitert und garantiert nichts angelegt wird.

**Warum:** `FoiMessageSerializer.validate_kind` (`serializers.py:371`) prüft gegen
`MESSAGE_KIND_USER_ALLOWED = [POST, PHONE, VISIT]` (`models/message.py:80`). Alles so
Angelegte ist ein Entwurf, und `/message/{id}/publish/` verlangt zusätzlich `is_postal`,
also `kind == post` — `phone`- und `visit`-Entwürfe lassen sich anlegen, aber nie
veröffentlichen. Das ist eine Inkonsistenz in froide selbst.

Die Message-API dient dem **Dokumentieren analoger Post**, die man auf Papier bekommen hat,
nicht dem Versenden.

### Ist der Web-Endpunkt nicht vielleicht nur undokumentiert?

Das war unser erster Verdacht. Nein.

**Gemessen am 2026-09-05:**

```
POST https://fragdenstaat.de/anfrage/<slug>/send/message/
  mit Bearer-Token → 302 → /account/login/
  ohne Auth        → 302 → /account/login/     ← identisch
```

Die Django-View ignoriert OAuth-Token vollständig. Session und CSRF, sonst nichts. Getestet
mit leeren Formulardaten, damit auch bei erfolgreicher Auth nichts hätte rausgehen können.

Wir haben außerdem geprüft, ob das Schema unvollständig sein könnte: es wird von
drf-spectacular aus dem laufenden Router erzeugt, und die registrierten Router im Quellcode
decken sich mit den Schema-Ressourcen. Auf `/api/v1/` gibt es keine verborgene Oberfläche.

**Folge:** Jeder Ablauf mit Antworten braucht einen Browser und einen angemeldeten Menschen.
Damit planen, nicht dagegen.

## 2. `POST /api/v1/request/` sendet sofort

Kein Entwurfsmodus, keine Vorschau, kein Zurück. `CreateRequestService.create_request()`
setzt `send_now = True` für jeden aktiven Nutzer und ruft `message.send()` im selben Request
(`services.py:200-289`). Das Modell `RequestDraft` ist im API-Router überhaupt nicht
registriert — Entwürfe gibt es nur in der Weboberfläche.

**Das umgekehrte Risiko ist genauso real:** `201 {"status": "success"}` beweist **nicht**,
dass die Mail rausging. `send_now` bleibt `False`, wenn der Nutzer inaktiv ist, wenn der
Kampagnen-Hook `pre_request_creation` `blocked` setzt (auf fragdenstaat.de aktiv: greift bei
`user.is_blocked` und bei jungen Konten mit mehr als 10 Anfragen pro IP), oder wenn man
mehrere `publicbodies` übergibt — dann entsteht ein **FoiProject** und die Anfragen werden
asynchron über Celery erzeugt. Danach immer mit `GET /request/{id}/` nachprüfen.

## 3. Rechtsgrundlage: nicht direkt wählbar — aber über zwei Schritte doch

`MakeRequestSerializer` nimmt genau sieben Felder:

```
publicbodies  int[]     Pflicht      >1 Element erzeugt ein FoiProject, keine Anfrage
subject       str       Pflicht      max 230
body          str       Pflicht
full_text     bool      optional     Default false
public        bool      optional     Default true
reference     str       optional     "kind:value"
tags          str[]     optional
```

Kein `law_type`. Die API fällt auf `publicbody.default_law` zurück, und
`get_applicable_law()` sortiert nach `("-meta", "-priority")` — man bekommt also fast immer
das **kombinierte Meta-Gesetz**, nicht das spezifische.

Konkret: Für eine Kommune in Rheinland-Pfalz landet ein LTranspG-Antrag (Gesetz 16) über die
API stillschweigend unter „LTranspG, VIG" (Gesetz 18). Die Mail trägt dann `letter_start`
und `letter_end` des Meta-Gesetzes und zitiert die falsche Norm.

### Der Umweg, der wirklich funktioniert

Der naheliegende Schluss — „die API kann kein LTranspG, nimm das Web-Formular" — ist zu
pessimistisch. Zwei Tatsachen ergeben zusammen einen gangbaren Weg.

**Erstens: `law` ist per PATCH schreibbar.** Am 2026-09-05 mit einem nebenwirkungsfreien
Diskriminator geprüft — PATCH mit einer nicht existierenden Gesetzes-URI. Ein schreibbares
Feld wird validiert und antwortet 400; ein read-only-Feld wird still ignoriert und antwortet
200:

```
PATCH /api/v1/request/379655/  {"law": ".../api/v1/law/999999/"}
  → 400 {"law": ["Ungültiger Hyperlink - Objekt existiert nicht."]}
  → das Feld wird validiert, ist also schreibbar. Geschrieben wurde nichts.
```

Das ist kein Hack. froide hat dafür ein eigenes Feature: `ConcreteLawForm`
(`forms/request.py:386`) existiert genau dafür, eine unter einem **Meta-Gesetz** gestellte
Anfrage nachträglich auf eines seiner `combined`-Gesetze zu verengen. Gesetz 18 kombiniert
`[16, 3]` — 16 zu setzen ist also die vorgesehene Handlung, kein Missbrauch.

**Zweitens: `full_text: true` entfernt den Wortlaut des Meta-Gesetzes aus der Mail.** Damit
wird der Text zu `Text + Name` und sonst nichts (`utils.py:213`). Wir haben die restliche
Vorlage geprüft: `mail_with_userinfo.txt` ergänzt nur Anfragenummer, Antwortadresse,
Upload-Link und einen gesetzesneutralen Footer. **Außerhalb von `letter_start`/`letter_end`
wird keine Norm zitiert.**

**Der vollständige API-Weg lautet also:**

1. `POST /api/v1/request/` mit `full_text: true` und einem selbsttragenden Text, der die
   gewünschte Norm zitiert — die Behörde bekommt exakt deinen Wortlaut, ohne Meta-Gesetz.
2. `PATCH /api/v1/request/{id}/` mit `{"law": ".../law/16/"}` — der Datensatz zeigt danach
   die spezifische Rechtsgrundlage.

Mail und Datensatz stimmen am Ende beide.

**Drei Einschränkungen, alle real:**

- `due_date` wird **bei der Erstellung** aus dem Default-Gesetz berechnet und vom PATCH
  **nicht** neu gerechnet. Hier macht das keinen Unterschied (16 und 18 haben beide einen
  Monat), aber wo die Gesetze unterschiedliche Fristen haben, bleibt die gespeicherte Frist
  falsch. Vorher prüfen.
- `full_text: true` entfernt alle sechs Pflichtbausteine auf einmal. Normzitierung,
  Kostenklausel, Fristbitte, Weiterleitungsbitte, Bitte um elektronische Antwort, Anrede und
  Grußformel muss man selbst liefern. Das gehört in eine Vollständigkeitsprüfung im Client,
  nicht ins Gedächtnis.
- PATCH ist **weniger** eingeschränkt als das Web-Formular. `ConcreteLawForm` bietet nur die
  `combined`-Gesetze des Meta-Gesetzes an und feuert das Signal `set_concrete_law`; PATCH
  akzeptiert jedes existierende Gesetz und feuert nichts. Bequem, heißt aber: ein Client kann
  stillschweigend eine Norm setzen, die gar nicht einschlägig ist. Auf die `combined`-Menge
  beschränken.

Derselbe PATCH-Endpunkt schreibt außerdem `refusal_reason, costs, description, summary,
status, resolution, tags`. Nur `public` ist read-only.

## 4. Dein Text wird gerahmt, und die Rahmung verdoppelt sich leicht

Bei `full_text: false` (Default) baut froide die Mail so:

```
letter_start (aus dem Gesetz)   z.B. "Antrag nach dem LTranspG\n\nGuten Tag,\n\nbitte senden Sie mir Folgendes zu:"
dein Text
letter_end   (aus dem Gesetz)   z.B. Normzitat, Bitte um Kosten-Vorabinfo, Fristbitte,
                                Weiterleitungsbitte und "Mit freundlichen Grüßen"
dein Name
```

Wer also eigene Anrede und eigene Grußformel schreibt, schickt der Behörde beides doppelt.
Genau diesen Fehler haben wir einmal produziert, bevor er aufgefallen ist.

Bei `full_text: true` bekommt man nur `Text + Name`. Alles andere — Anrede, Normzitat, Frist,
Grußformel — muss im eigenen Text stehen. Es wird nichts ergänzt.

**Was kaum jemand weiß:** Im **Web-Formular** heißt dieselbe Option „Vorlage anpassen" und
füllt das Textfeld mit dem *fertig zusammengesetzten* Text vor, sodass man editiert statt bei
null anzufangen (es gibt sogar „Text auf Vorlagenversion zurücksetzen"). Über die **API**
bekommt man nichts vorbefüllt. Wer die Vorlage ändern will — etwa um einen Kostendeckel
einzufügen —, für den ist das Web-Formular mit dieser Checkbox der saubere Weg.

### Antworten werden gar nicht gerahmt

Eine Folgenachricht über `/anfrage/<slug>/send/message/` bekommt **kein** `letter_start`,
**kein** `letter_end` und **keinen** Namen. Anrede und Grußformel sind im eigenen Text
Pflicht. Das ist die exakte Umkehrung der Regel für die Erstanfrage und gehört als zwei
getrennte Prüfungen abgebildet.

Das Antwortformular ist mit einem Skelett vorbefüllt, das den Platzhalter `…` (U+2026)
enthält — und `validate_no_placeholder` lehnt jeden Text ab, der ihn enthält. Das Skelett
ersetzen, nicht ergänzen.

## 5. Die API validiert deutlich weniger als das Web-Formular

| Regel | Web-Formular | REST-API |
|---|---|---|
| Betreff ≥ 8 Zeichen | ja | **nein** |
| Betreff ≤ 230 | ja | ja |
| `slugify(Betreff)` ≥ 4 Zeichen | ja | **nein** |
| Text ≥ 8 Zeichen | ja | **nein** |
| Text ≤ 5000 Zeichen | ja (außer `is_trusted`) | **nein** |
| Platzhalter `…` abgelehnt | ja | **nein** |
| fehlerhafte `reference` | still verworfen | **400 „Reference not clean"** |

Ein Client sollte trotzdem die strengeren Web-Regeln durchsetzen. Sonst entstehen über die
API Anfragen, die die Weboberfläche danach nicht mehr bearbeiten lässt.

## 6. Vorbefüllte Formular-URLs brechen bei etwa 4 KB

Der empfohlene Umweg für das Rechtsgrundlagen-Problem ist eine vorbefüllte Formular-URL. Die
hat eine harte Grenze.

**Am 2026-09-05 per Bisektion gemessen: 4104 Bytes → HTTP 200, 4105 Bytes → HTTP 400.**
Unabhängig von anderen Headern und identisch über HTTP/1.1 und h2 — also ein URI-Längenlimit,
kein Header-Limit. Ein typischer Anfragetext von 4000 Zeichen überschreitet es nach dem
URL-Encoding.

Zweistufiger Ausweg: das Formular nur mit `subject`, `law_type` und `hide_publicbody=1`
öffnen (rund 220 Bytes) und den Text aus einer Datei oder der Zwischenablage einfügen lassen.

Query-Parameter, die `MakeRequestView.get_initial()` tatsächlich liest: `subject`, `body`,
`tags`, `law_type`, `responsibility`, `jurisdiction`, `draft`, `ref` (**nicht** `reference`),
`redirect` (**nicht** `redirect_url`), `public` und `full_text` als `"1"`/`"0"`, dazu `email`,
`first_name`, `last_name`. `address` und `language` werden **nicht** aus der Query gelesen.

## 7. Rate-Limits — und eines, das nicht durchgesetzt wird

Auf fragdenstaat.de (`fragdenstaat_de/settings/base.py:734`), ausgenommen sind `trusted()`-Konten:

| Anfragen | Fenster | | Nachrichten | Fenster |
|---|---|---|---|---|
| 5 | 5 Min | | 2 | 5 Min |
| 6 | 6 h | | 6 | 6 h |
| 10 | 24 h | | 8 | 24 h |
| 20 | 7 d | | | |

`request_throttle` **wird** im API-Pfad durchgesetzt
(`api_views/request.py:240`, `@throttle_action((MakeRequestThrottle,))`).

`message_throttle` **nicht** — `FoiMessageViewSet.create` hat kein `throttle_action`;
`check_throttle(..., FoiMessage)` steht nur in den Web-Views. Ein anständiger Client hält
sich trotzdem daran.

Nach den Nutzungsbedingungen (B.1.4) führt **der Versuch, die Limits zu umgehen, zur
einmonatigen Kontosperre.** Keine Retry-Schleifen bauen.

## 8. Datenschutzfallen

- `public: true` ist Default. Alles — der vollständige Mailverlauf, freigegebene Anhänge,
  hochgeladene Dokumente — wird öffentlich lesbar und unter **CC0** freigegeben.
- Das Antwortformular führt die **Postadresse** des Nutzers vorbefüllt mit und bietet eine
  Checkbox „Adresse mitsenden". Bei einer öffentlichen Anfrage ist ein versehentlicher Haken
  eine Datenpanne. Vor dem Absenden prüfen, dass er aus ist.
- Keine personenbezogenen Daten Dritter in den Text. Er ist öffentlich und unwiderruflich.

## 9. Wo froides eigene Dokumentation falsch ist

Bevor man ihr vertraut:

- `froide/docs/api.rst` behauptet, POST/PUT/DELETE akzeptierten **Basic Authentication**.
  Tun sie nicht. `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES` (`settings.py:530`) kennt
  nur `OAuth2Authentication` und `SessionAuthentication`, und `fragdenstaat_de` überschreibt
  das nicht.
- Dieselbe Datei verweist auf `/api/v1/docs/`. Das liefert **404**. Die interaktive
  Dokumentation liegt unter `/api/v1/schema/swagger-ui/`.
- Die Scope-Liste auf <https://fragdenstaat.de/api/> lässt `write:attachment` weg, das im
  Quellcode und im Schema existiert und für Uploads zwingend ist.

## 10. OAuth-Fallstricke

- `ALLOWED_REDIRECT_URI_SCHEMES` ist `["https", "fragdenstaat"]`. **`http://localhost` wird
  abgelehnt** — der übliche lokale Entwicklungs-Redirect funktioniert nicht. Beide URIs
  registrieren: `https://localhost:<port>/callback` und `fragdenstaat://callback`.
- Client-Typ `Public` erzwingt PKCE (S256), und das Client-Secret wird nur als Hash
  gespeichert. Damit entfällt die Frage, wo man ein Secret aufbewahrt.
- Es gibt keinen Client-Credentials-Flow für Nutzerdaten. Jede Anfrage gehört zu einem Konto.
- Refresh-Token gelten **180 Tage**.
- **`write:request` wird leicht übersehen und ist zwingend** für `POST /api/v1/message/`
  (`validate_request` ruft `can_write_foirequest`) und für `PATCH /request/{id}/`, das es
  *zusätzlich* zu `make:request` braucht.
- Ein lokaler HTTPS-Listener mit selbstsigniertem Zertifikat ist für Menschen ein guter
  Default, aber ein automatisierter oder abgesicherter Browser kommt an
  `NET::ERR_CERT_AUTHORITY_INVALID` nicht vorbei, und der Login läuft in einen Timeout. Einen
  manuellen Weg zum Kopieren der URL bereithalten.

## 11. Anhänge

Uploads laufen dreistufig über tus: Upload anlegen (`POST /api/v1/upload/` mit
`Tus-Resumable`, `Upload-Length`, `Upload-Metadata`), Bytes schieben
(`PATCH /api/v1/upload/{guid}/`), dann anhängen (`POST /api/v1/attachment/` mit den URIs von
Nachricht und Upload). Braucht `write:attachment`.

Zulässige Typen für Postnachrichten sind `PDF_FILETYPES + IMAGE_FILETYPES` — sieben
PDF-MIME-Varianten plus `image/png`, `image/jpeg`, `image/jpg`, `image/gif`. Der Typ wird per
libmagic aus den ersten 1024 Bytes bestimmt; die Dateiendung ist irrelevant.

Da nur `kind: post` veröffentlicht werden kann, sind Uploads zum Dokumentieren gescannter
Briefe nützlich — nicht zum Anhängen von Dateien an eine ausgehende Anfrage.

---

## Was wir probiert haben, das nicht funktioniert hat

Die Sackgassen, weil sie die meiste Zeit gekostet haben:

| Versuch | Ergebnis |
|---|---|
| `POST /api/v1/message/` mit `kind: email` zum Antworten | 400, kind abgelehnt |
| Bearer-Token gegen die Web-View `send/message/` | 302 zum Login, Token ignoriert |
| Vorbefüllte Formular-URL mit dem ganzen 4-KB-Text | HTTP 400 ab ca. 4104 Bytes |
| `fds-mcp login` mit HTTPS-Localhost-Listener im automatisierten Browser | Zertifikats-Interstitial nicht passierbar, Timeout nach 300 s |
| Spezifische Rechtsgrundlage in einem Aufruf setzen | still unter dem Meta-Gesetz gelandet — aber siehe §3, mit `full_text: true` plus PATCH geht es |
| `http://localhost/callback` als Redirect-URI registrieren | von der Schema-Allowlist abgelehnt |

## Was wir dabei falsch gemacht haben

Zur Kalibrierung — das waren unsere Fehler, nicht die der Plattform:

- Wir haben zuerst behauptet, Status, Ergebnis, Tags und Rechtsgrundlage seien nur in der
  Weboberfläche setzbar. Falsch: `PATCH /request/{id}/` schreibt alle.
- Wir haben die kind-Beschränkung zuerst der Permission `OnlyPostalMessagesWritable`
  zugeschrieben. Falsch: die implementiert nur `has_object_permission`, das DRF bei `create`
  gar nicht aufruft. Der echte Riegel ist `validate_kind`.
- Wir haben zuerst eine Minimal-Scope-Menge ohne `write:request` veröffentlicht. Jedes
  `POST /api/v1/message/` wäre gescheitert.
- Wir haben eigene Anrede und Grußformel in einen Anfragetext mit `full_text: false`
  geschrieben — das verdoppelt, was die Gesetzesvorlage ohnehin liefert.
- Unsere URL-Längenprüfung hat die URL nachgebildet statt die tatsächlich erzeugte zu messen
  und lag 18 Bytes zu niedrig — ein Fenster, in dem der Test grün war und der Server 400 lieferte.
- Wir haben geschlossen „die API kann die spezifische Rechtsgrundlage nicht" und dort
  aufgehört. Sie kann es, über `full_text: true` plus PATCH — und froide hat für die zweite
  Hälfte sogar ein eigenes Feature. Die allgemeine Lehre: „der naheliegende Aufruf
  unterstützt X nicht" ist nicht dasselbe wie „die API kann X nicht". Vor der
  Limitierungs-Meldung nach dem zweiten Schritt suchen.
