# Wartungsmelder

Legt Wartungen in Uptime Kuma an, wenn an einem Gerät gearbeitet wird, und entfernt sie
wieder, sobald es vorbei ist. Ausgelöst wird von außen über HTTP — beim Wandpanel
„HA Wall Eurasburg" macht das die App selbst.

## Warum ein Add-on und nicht Teil des Panels

Uptime Kuma hat für Wartungen **keine REST-API**. Das läuft über Socket.io, und die Anmeldung
braucht **Benutzername und Passwort** — ein API-Key genügt dafür nicht, der gilt nur für
`/metrics`.

Uptime Kuma kennt keine Rollen: Diese Zugangsdaten sind Vollzugriff auf deine Überwachung.
Hier liegen sie in den Add-on-Optionen, auf demselben Host wie Uptime Kuma, und verlassen ihn
nie. Im Wandpanel lägen sie im Klartext auf einem Gerät ohne Anmeldekennwort.

## Einrichten

| Option | Bedeutung |
|---|---|
| `kuma_url` | Adresse von Uptime Kuma. **Nicht `localhost`**: Das ist innerhalb dieses Add-on-Containers der Container selbst, nicht der Host — Uptime Kuma ist so nicht erreichbar, und der Fehler sieht aus wie ein falsches Passwort. Also `http://<ip-des-ha-hosts>:3001`. Das Add-on warnt beim Start, wenn hier `localhost` steht. |
| `kuma_benutzer` / `kuma_passwort` | Anmeldung bei Uptime Kuma |
| `zugriffsschluessel` | Schutz dieser Schnittstelle. **Leer heißt offen** — jeder im Netz könnte dann Wartungen anlegen und löschen. Das Add-on warnt beim Start. |
| `monitor_namen` | Welche Monitore die Wartung betrifft. **Namen, nicht IDs**: IDs ändern sich, wenn ein Monitor neu angelegt wird. |
| `statusseite` | Titel oder Kurzname der Statusseite, auf der die Wartung erscheinen soll |
| `zeitzone` | Standard `Europe/Berlin` |

**Nach dem ersten Start `GET /selbsttest` aufrufen.** Er meldet sich an, liest Wartungen,
Monitore und Statusseiten und sagt, was funktioniert hat. Der Grund steht unten unter
„Versionslage".

## Schnittstelle

Alle Aufrufe außer `/gesundheit` brauchen den Schlüssel, entweder als Kopfzeile
`X-Schluessel: …` oder als `?schluessel=…`.

| Aufruf | Wirkung |
|---|---|
| `GET /gesundheit` | Läuft der Dienst, ist er konfiguriert? Ohne Schlüssel erreichbar, damit Uptime Kuma dieses Add-on selbst überwachen kann. Gibt keine Zugangsdaten, keine Monitornamen und keine Kuma-Inhalte heraus — nur Anzahlen. Antwortet **503**, wenn Zugangsdaten fehlen, die Bibliothek nicht geladen ist oder `/data` nicht schreibbar ist, sonst 200 mit dem Wort `HAWALL-OK` im Körper. |
| `GET /selbsttest` | Anmeldung und Lesezugriffe gegen die echte Instanz prüfen |
| `GET /wartung` | Was in der Ablage steht und was in Uptime Kuma tatsächlich offen ist |
| `POST /wartung/<schlüssel>` | Wartung anlegen (ersetzt eine vorhandene mit demselben Schlüssel) |
| `DELETE /wartung/<schlüssel>` | Wartung beenden |
| `POST /aufraeumen` | Aufräumen von Hand nachholen |

### Anlegen

```
POST /wartung/wandpanel-update
{
  "titel": "Wandpanel: Update 1.0.13 → 1.0.14",
  "beschreibung": "Die App installiert ein Update und startet neu.",
  "strategie": "manual",
  "dauer_minuten": 20
}
```

`strategie` kennt: `manual` (kein Zeitplan, wird durch Löschen beendet — der Normalfall für
„jetzt wird gearbeitet"), `single` (einmaliges Fenster von jetzt bis `dauer_minuten`),
`recurring-interval` (alle `intervall_tage`), `recurring-weekday` (`wochentage`),
`recurring-day-of-month` (`monatstage`), `cron` (`cron` plus `dauer_minuten`).
Bei den wiederkehrenden kommt `zeitfenster` dazu, z. B.
`[{"hours":2,"minutes":0},{"hours":3,"minutes":0}]`.

`monitore` und `statusseite` lassen sich pro Aufruf überschreiben; ohne Angabe gelten die
Add-on-Optionen.

## Wie Wartungen wiedergefunden werden

Auf **zwei** Wegen, und der zweite ist der wichtigere:

1. `/data/wartungen.json` — Zuordnung Schlüssel → Wartungs-ID.
2. Ein Merker in der **Beschreibung** der Wartung: `[wartungsmelder:<schlüssel>]`.

Geht die Datei verloren (Add-on neu aufgesetzt, Datenträger getauscht), ließen sich die
Wartungen ohne den Merker nicht mehr zuordnen und würden für immer in Uptime Kuma stehen.

**Beim Start wird beidseitig aufgeräumt:** Einträge in der Ablage, deren Wartung es in Uptime
Kuma nicht mehr gibt, fallen weg; Wartungen mit unserem Merker, die in der Ablage fehlen,
werden in Uptime Kuma gelöscht. Der zweite Fall entsteht, wenn das Add-on oder der Host mitten
in einer Wartung neu startet — ohne Aufräumen stünde die Statusseite dauerhaft auf „in
Wartung", und das fällt niemandem auf.

**Zweimal derselbe Schlüssel bedeutet Ersetzen, nie Verdoppeln.** Sonst stehen nach fünf
Updates fünf offene Wartungen für dasselbe Gerät in der Liste.

## Was ohne Zuordnung passiert

Eine Wartung **ohne zugeordnete Monitore unterdrückt keinen einzigen Alarm**, und ohne
zugeordnete Statusseite erscheint sie dort nicht. Sie ist dann angelegt, in der Wartungsliste
sichtbar — und wirkungslos. Deshalb macht das Add-on nach dem Anlegen immer beide
Zuordnungen und schreibt ins Protokoll, welche Monitore es **nicht** gefunden hat.

## Versionslage

`uptime-kuma-api` ist auf **1.2.1 festgenagelt** und verspricht offiziell nur Uptime Kuma
**1.17.0 – 1.23.2**. Hier läuft **2.5.5**.

Am Quelltext von Kuma 2.5.5 nachgeprüft: Die Wartungs-Ereignisse heißen dort unverändert
(`addMaintenance`, `addMonitorMaintenance`, `addMaintenanceStatusPage`, …),
`add_maintenance(**kwargs)` gibt die Felder direkt durch und kann deshalb nicht an einer
Signatur brechen, und eine harte Versionssperre hat die Bibliothek nicht. Es *sollte* also
gehen — „sollte" ist aber keine Grundlage, und deshalb gibt es `GET /selbsttest`.

**Wer die Fassung der Bibliothek hochzieht, lässt danach den Selbsttest laufen.**

## Dieses Add-on selbst überwachen

In Uptime Kuma ein Monitor vom Typ **HTTP(s) - Keyword**:

| Feld | Wert |
|---|---|
| URL | `http://<ha-host>:8099/gesundheit` |
| Keyword | `HAWALL-OK` |

Nach Text und nicht nach Struktur, weil Uptime Kuma genau das anbietet — dasselbe Schlüsselwort
benutzt das Wandpanel unter `/api/gesundheit`. Wer es umbenennt, muss den Monitor nachziehen.
