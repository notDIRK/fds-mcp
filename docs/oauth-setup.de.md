# OAuth einrichten

*[English version](oauth-setup.md) — die englische Fassung ist die Quelle, diese hier
folgt ihr.*

`fds-mcp` spricht mit fragdenstaat.de als **Sie**. Es gibt kein Dienstkonto und keinen
einfachen API-Key: der einzige programmatische Weg ist eine OAuth-2.0-Anwendung, die auf
Ihrem eigenen Konto registriert ist.

Alles Folgende wurde am 2026-09-05 an einem echten Konto durchgespielt, Fehler
eingeschlossen.

---

## 1. Anwendung registrieren

Gehen Sie angemeldet auf **<https://fragdenstaat.de/account/applications/>**. Alle
Kontoseiten sind durch `recent_auth_required` geschützt; eine alte Sitzung reicht also
nicht — Sie werden erneut nach Ihrem Passwort gefragt.

![Die Liste der OAuth-Anwendungen](screenshots/01-applications-list.png)

Klicken Sie auf *Neue Anwendung* / *Register new application*:

![Das ausgefüllte Registrierungsformular](screenshots/02-register-application.png)

| Feld | Wert | Warum |
|---|---|---|
| **Name** | `fds-mcp` | wird auf dem Zustimmungsdialog angezeigt |
| **Description** | beliebig | wird auf dem Zustimmungsdialog angezeigt |
| **Homepage** | Ihr Fork oder `https://github.com/dwolbeck/fds-mcp` | |
| **Redirect URIs** | `https://localhost:8765/callback`<br>`fragdenstaat://callback` | eine pro Zeile; registrieren Sie **beide** — siehe Schritt 3 |
| **Client type** | `Public` | erzwingt PKCE und macht das Client-Secret gegenstandslos |
| **Authorization grant type** | `Authorization code` | der einzige Flow, der für Nutzerdaten funktioniert |
| Post-Logout Redirect URIs | *leer* | |
| Allowed Origins | *leer* | |

### Regeln für die Redirect-URI — lesen, bevor Sie einen Formularfehler bekommen

`OAUTH2_PROVIDER.ALLOWED_REDIRECT_URI_SCHEMES` ist auf fragdenstaat.de
`["https", "fragdenstaat"]`. Folgen:

- **`http://localhost:...` wird abgelehnt.** Der übliche lokale Entwicklungs-Redirect
  funktioniert nicht.
- `https://localhost:8765/callback` wird akzeptiert — geprüft wird das Schema, nicht der
  Host.
- `fragdenstaat://callback` wird als privates Schema akzeptiert.

### Client-Typ: nehmen Sie `Public`

Mit `Public` liefert `is_pkce_required()` wahr, und der Flow ist durch PKCE (S256)
geschützt. Das Formular erzeugt weiterhin ein Client-Secret, aber fragdenstaat.de
speichert nur dessen Hash, und öffentliche Clients senden es nie. Damit entfällt die ganze
Frage, wo man ein Secret aufbewahrt — es gibt nichts aufzubewahren.

`Confidential` wählen Sie nur für ein serverseitiges Deployment, in dem Sie das Secret
schützen können.

## 2. Die Client-Daten hinterlegen

```bash
fds-mcp configure \
  --client-id <Ihre Client-ID> \
  --redirect-uri "https://localhost:8765/callback"
```

Das nimmt die Standard-Scopes — `read:user read:request make:request` —, also genau das,
was die Werkzeuge in diesem Repository tatsächlich aufrufen. Geschrieben nach
`~/.config/fds-mcp/config.json` mit Modus `0600`.

### Scopes

**Gewähren Sie so wenig wie möglich.** Ein Token ist nur so gefährlich wie seine Scopes,
es lebt 180 Tage, und es liegt in einer Datei, die ein MCP-Server bei jedem Aufruf liest.
Rein lesende Nutzung braucht `read:user read:request`; lassen Sie `make:request` weg, und
`submit_request` kann nie feuern, was auch immer mit den fünf Gates passiert.

Fügen Sie `write:request`, `write:message` oder `write:attachment` **nicht**
„sicherheitshalber" hinzu: kein Werkzeug in diesem Repository benutzt sie (Postbriefe
dokumentieren ist als bekannte Lücke geführt, nicht als Funktion), sie würden also eine
Fähigkeit gewähren, die hier niemand braucht. Nehmen Sie sie erst dazu, wenn Sie auf
diesem Server aufbauen und wissen, warum.

| Scope | Gebraucht für |
|---|---|
| `read:user` | `whoami`, wissen, welches Konto Sie sind |
| `read:profile`, `read:email` | reichere Antwort von `/api/v1/user/`, optional |
| `read:request` | `list_my_requests`, `get_request` bei nicht-öffentlichen Anfragen |
| `make:request` | `submit_request`, und das Request-Viewset überhaupt |
| `write:request` | *von diesem Server nicht benutzt.* `POST /api/v1/message/` ruft `validate_request` → `can_write_foirequest()` auf, und `PATCH /request/{id}/` braucht es zusätzlich zu `make:request` — relevant nur, wenn Sie den Server erweitern |
| `write:message` | *von diesem Server nicht benutzt.* Postverkehr dokumentieren |
| `write:attachment` | *von diesem Server nicht benutzt.* Uploads über den tus-Endpunkt |

Der Screenshot des Zustimmungsdialogs weiter unten entstand beim Ausprobieren der
vollständigen Scope-Liste; er zeigt deshalb mehr Berechtigungen, als `fds-mcp configure`
standardmäßig anfordert. Ihrer sollte kürzer sein.

## 3. Anmelden

```bash
fds-mcp login
```

Das startet einen lokalen HTTPS-Listener auf Port 8765, öffnet den Browser und wartet auf
den Callback.

![Der Zustimmungsdialog](screenshots/03-oauth-consent.png)

Der Dialog listet genau die Scopes, die Sie konfiguriert haben. Bestätigen Sie, und die
Tokens landen in `~/.config/fds-mcp/tokens.json`, Modus `0600`.

### Wenn der HTTPS-Listener nicht funktioniert

Der Listener benutzt ein selbstsigniertes Zertifikat, der Browser zeigt also
`NET::ERR_CERT_AUTHORITY_INVALID` und blockiert den Redirect. In einem normalen Browser
klicken Sie auf *Erweitert → Weiter zu localhost*. In einem abgeschotteten oder
automatisierten Browser können Sie das womöglich nicht, und `fds-mcp login` läuft dann mit
`No OAuth callback received within 300s.` in einen Timeout.

Nehmen Sie stattdessen den manuellen Modus — er braucht weder Listener noch Zertifikat:

```bash
fds-mcp configure --client-id <id> --redirect-uri "fragdenstaat://callback" --scopes "..."
fds-mcp login --manual
```

Nach der Bestätigung versucht der Browser, `fragdenstaat://callback?code=...&state=...` zu
öffnen, und scheitert, weil für dieses Schema keine Anwendung registriert ist. **Dieses
Scheitern ist das erwartete Ergebnis.** Kopieren Sie die ganze URL aus der Adresszeile und
fügen Sie sie in die Eingabeaufforderung zurück. Deshalb registriert Schritt 1 beide
Redirect-URIs: Sie können zwischen ihnen wechseln, ohne die Anwendung anzufassen.

## 4. Prüfen

```bash
fds-mcp whoami
fds-mcp status
```

`status` gibt Client-ID, Redirect-URI, gewährte Scopes, die Gültigkeit der Tokens und den
aktuellen Stand der lokalen Throttle-Buchführung aus. Es gibt niemals ein Token aus.

## Token-Lebensdauer

Refresh-Tokens sind **180 Tage** gültig
(`OAUTH2_PROVIDER.REFRESH_TOKEN_EXPIRE_SECONDS`). `fds-mcp` erneuert automatisch, solange
Sie es innerhalb dieses Fensters benutzen. Danach führen Sie `fds-mcp login` erneut aus.

`fds-mcp logout` löscht die gespeicherten Tokens lokal. Um den Zugriff serverseitig zu
widerrufen, nutzen Sie <https://fragdenstaat.de/account/authorized-tokens/>; um die
Anwendung ganz zu löschen, deren Seite unter
<https://fragdenstaat.de/account/applications/>.

## Was liegt wo

| Datei | Modus | Inhalt |
|---|---|---|
| `~/.config/fds-mcp/config.json` | 0600 | Client-ID, Redirect-URI, Scopes. Kein Secret bei öffentlichen Clients. |
| `~/.config/fds-mcp/tokens.json` | 0600 | Access-Token, Refresh-Token, Ablauf, gewährte Scopes |
| `~/.config/fds-mcp/callback-{cert,key}.pem` | 0600 | selbstsigniertes Zertifikat für den lokalen Listener, beim ersten `login` erzeugt |

Nichts davon gehört in die Versionsverwaltung. Die mitgelieferte `.gitignore` deckt es ab,
aber die Dateien liegen ohnehin außerhalb des Repositories.

## Und das Browser-Feature?

`send_reply_via_browser` benutzt **kein** OAuth. Es kann es nicht: die Web-View
`/anfrage/<slug>/send/message/` ignoriert Bearer-Tokens vollständig und antwortet mit und
ohne Token identisch mit HTTP 302 auf `/account/login/` (gemessen 2026-09-05,
`tests/test_api_contract.py`). Das Werkzeug trägt stattdessen eine angemeldete
Browser-Sitzung aus einem Profil, auf das `FDS_MCP_BROWSER_PROFILE` zeigt. Dieses Profil
ist ein zweites, unabhängiges Zugangsmittel zu Ihrem Konto — behandeln Sie es wie ein
Token, nicht wie einen Cache. Die Risiken stehen im
[README](../README.de.md#antworten-senden).
