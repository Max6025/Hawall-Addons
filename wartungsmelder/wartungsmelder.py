"""Wartungen in Uptime Kuma anlegen und wieder wegräumen.

AUFGABE

Wenn an einem Gerät gearbeitet wird -- Update, Neustart, Wartung vor Ort -- soll in Uptime
Kuma eine Wartung stehen, damit die Statusseite nicht rot wird und niemand einen Alarm
bekommt. Und sobald es vorbei ist, soll sie wieder weg sein. Ausgelöst wird von außen, in
diesem Fall vom Wandpanel.

WARUM SOCKET.IO UND NICHT REST

Uptime Kuma hat für Wartungen keine REST-API. Alles läuft über Socket.io, und die Anmeldung
braucht Benutzername und Passwort -- ein API-Key genügt NICHT, der gilt nur für /metrics.
Deshalb uptime-kuma-api, und deshalb dieses Add-on auf demselben Host wie Uptime Kuma: Die
Zugangsdaten sind Vollzugriff auf die Überwachung (Uptime Kuma kennt keine Rollen) und sollen
den Host nicht verlassen.

DER SCHLÜSSEL

Jede Wartung hängt an einem SCHLÜSSEL, den der Aufrufer mitgibt (z. B. "wandpanel-update").
Darüber läuft alles: anlegen, ersetzen, beenden. Zweimal derselbe Schlüssel bedeutet nie
zwei Wartungen, sondern Ersetzen -- sonst sammeln sich nach einigen Updates fünf offene
Wartungen für dasselbe Gerät.

ZWEI WEGE, EINE WARTUNG WIEDERZUFINDEN

1. /data/wartungen.json -- die Zuordnung Schlüssel -> Wartungs-ID.
2. Ein Merker in der BESCHREIBUNG der Wartung selbst ("[wartungsmelder:<schluessel>]").

Der zweite ist der wichtigere. Geht die Datei verloren (Add-on neu aufgesetzt, Datenträger
getauscht), ließen sich die Wartungen sonst nicht mehr zuordnen und würden für immer in
Uptime Kuma stehen. Mit dem Merker findet das Aufräumen sie trotzdem.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

# Die Bibliothek erst hier importieren, damit ein Importfehler als saubere Meldung im
# Add-on-Protokoll landet und nicht als Stapelspur beim Start.
try:
    from uptime_kuma_api import UptimeKumaApi, MaintenanceStrategy, UptimeKumaException
    BIBLIOTHEK_FEHLER = None
except Exception as e:  # pragma: no cover - hängt an der Umgebung
    UptimeKumaApi = None
    MaintenanceStrategy = None
    UptimeKumaException = Exception
    BIBLIOTHEK_FEHLER = str(e)


# --- Einstellungen ----------------------------------------------------------------------------

KUMA_URL = os.environ.get("KUMA_URL", "http://localhost:3001").rstrip("/")
KUMA_BENUTZER = os.environ.get("KUMA_BENUTZER", "")
KUMA_PASSWORT = os.environ.get("KUMA_PASSWORT", "")
ZUGRIFFSSCHLUESSEL = os.environ.get("ZUGRIFFSSCHLUESSEL", "")
STATUSSEITE = os.environ.get("STATUSSEITE", "").strip()
ZEITZONE = os.environ.get("ZEITZONE", "Europe/Berlin")
# Die Liste kommt zeilenweise aus bashio, getrennt mit dem Datensatztrenner 0x1e.
MONITOR_NAMEN = [t.strip() for t in os.environ.get("MONITOR_NAMEN", "").split("\x1e") if t.strip()]

PORT = int(os.environ.get("PORT", "8129"))
ABLAGE = os.environ.get("ABLAGE", "/data/wartungen.json")
# Bleibt None, solange die Ablage schreibbar ist; sonst der Grund, im Klartext.
ABLAGE_FEHLER = None
MERKER = "wartungsmelder"

logging.basicConfig(
    level=getattr(logging, os.environ.get("PROTOKOLLSTUFE", "info").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("wartungsmelder")

app = Flask(__name__)
# Ein Schloss um jeden Kuma-Zugriff. Socket.io-Sitzungen sind nicht dafür gedacht, von
# mehreren Anfragen gleichzeitig benutzt zu werden, und zwei Auslöser zur selben Zeit sind
# bei einem Update durchaus möglich (Panel meldet "an", Wachhund meldet "an").
schloss = threading.Lock()


# --- Beständige Ablage -----------------------------------------------------------------------

def ablage_lesen():
    try:
        with open(ABLAGE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        # Eine kaputte Datei darf den Dienst nicht lahmlegen -- der Merker in der Beschreibung
        # reicht zum Aufräumen.
        log.warning("Ablage %s nicht lesbar (%s). Es wird mit leerer Zuordnung weitergemacht.", ABLAGE, e)
        return {}


def ablage_schreiben(d):
    """Die Zuordnung ablegen. Scheitert das, wird es GEMELDET, nicht verschluckt.

    Eine nicht schreibbare Ablage ist der eine Fall, in dem alles andere weiter funktioniert
    und trotzdem etwas kaputt ist: Die Wartung wird in Uptime Kuma angelegt, die ID ist beim
    nächsten Start aber nur noch über den Merker in der Beschreibung auffindbar. Deshalb
    steht der Fehler in `/gesundheit` und macht die Antwort dort zu 503 -- sonst merkt es
    niemand, bis die Statusseite dauerhaft auf "in Wartung" steht.
    """
    global ABLAGE_FEHLER
    try:
        ordner = os.path.dirname(os.fspath(ABLAGE))
        if ordner:
            os.makedirs(ordner, exist_ok=True)
        # Erst daneben schreiben, dann umbenennen: Ein Stromausfall mitten im Schreiben soll
        # keine halbe Datei hinterlassen.
        tmp = os.fspath(ABLAGE) + ".neu"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, ABLAGE)
        ABLAGE_FEHLER = None
        return True
    except Exception as e:
        ABLAGE_FEHLER = str(e)
        log.error("Ablage %s nicht schreibbar: %s", ABLAGE, e)
        return False


# --- Verbindung -------------------------------------------------------------------------------

class KumaFehler(Exception):
    """Fehler, der dem Aufrufer als Klartext zugestellt wird."""

    def __init__(self, code, meldung):
        super().__init__(meldung)
        self.code = code
        self.meldung = meldung


def mit_kuma(arbeit):
    """Verbinden, anmelden, `arbeit(api)` ausführen, trennen.

    Pro Vorgang neu verbunden, nicht dauerhaft offen: Die Auslöser kommen selten (ein paar Mal
    pro Woche), und eine Sitzung, die tagelang offen steht, ist beim nächsten Kuma-Neustart
    stillschweigend tot -- dann schlägt der Aufruf fehl, wenn man ihn am dringendsten braucht.
    """
    if BIBLIOTHEK_FEHLER:
        raise KumaFehler(500, f"uptime-kuma-api ließ sich nicht laden: {BIBLIOTHEK_FEHLER}")
    if not KUMA_BENUTZER or not KUMA_PASSWORT:
        raise KumaFehler(500, "Benutzername oder Passwort für Uptime Kuma fehlen in den Add-on-Optionen.")

    api = None
    try:
        api = UptimeKumaApi(KUMA_URL, timeout=20)
    except Exception as e:
        raise KumaFehler(502, f"Uptime Kuma unter {KUMA_URL} nicht erreichbar: {e}")

    try:
        try:
            api.login(KUMA_BENUTZER, KUMA_PASSWORT)
        except Exception as e:
            raise KumaFehler(401, f"Anmeldung bei Uptime Kuma fehlgeschlagen: {e}")
        return arbeit(api)
    finally:
        try:
            api.disconnect()
        except Exception:
            pass


# --- Wartungen --------------------------------------------------------------------------------

def beschreibung_mit_merker(text, schluessel):
    """Der Merker macht die Wartung ohne die Ablage-Datei wiederfindbar."""
    return f"{text}\n\n[{MERKER}:{schluessel}]"


def merker_lesen(beschreibung):
    m = re.search(rf"\[{MERKER}:([^\]]+)\]", beschreibung or "")
    return m.group(1) if m else None


def unsere_wartungen(api):
    """Alle Wartungen, die von diesem Add-on stammen -- am Merker erkannt."""
    treffer = {}
    for w in api.get_maintenances():
        s = merker_lesen(w.get("description"))
        if s:
            treffer.setdefault(s, []).append(w)
    return treffer


def zuordnen(api, wartung_id, monitor_namen, statusseite):
    """Monitore und Statusseite zuordnen.

    OHNE DAS GREIFT DIE WARTUNG INS LEERE. Eine Wartung ohne zugeordnete Monitore unterdrückt
    keinen einzigen Alarm, und ohne zugeordnete Statusseite erscheint sie dort nicht -- sie ist
    dann angelegt, sichtbar in der Wartungsliste, und wirkungslos. Genau der Fehler, den man
    erst merkt, wenn nachts trotzdem ein Alarm kommt.
    """
    ergebnis = {"monitore": [], "nicht_gefunden": [], "statusseite": None}

    alle = {m.get("name"): m.get("id") for m in api.get_monitors()}
    ids = []
    for name in monitor_namen:
        if name in alle:
            ids.append({"id": alle[name]})
            ergebnis["monitore"].append(name)
        else:
            ergebnis["nicht_gefunden"].append(name)
    if ids:
        api.add_monitor_maintenance(wartung_id, ids)

    if statusseite:
        seiten = {s.get("title"): s.get("id") for s in api.get_status_pages()}
        # Auch der Kurzname (slug) zählt: In der Add-on-Option steht oft das, was in der URL
        # steht, nicht der Anzeigetitel.
        seiten.update({s.get("slug"): s.get("id") for s in api.get_status_pages()})
        sid = seiten.get(statusseite)
        if sid is not None:
            api.add_status_page_maintenance(wartung_id, [{"id": sid}])
            ergebnis["statusseite"] = statusseite
        else:
            ergebnis["statusseite"] = None
            log.warning("Statusseite %r nicht gefunden. Vorhanden: %s",
                        statusseite, ", ".join(str(k) for k in seiten if k))
    return ergebnis


def jetzt():
    return datetime.now(timezone.utc).astimezone()


def felder_fuer(strategie, daten):
    """Die Felder je Strategie.

    Basisfelder werden IMMER mitgeschickt, auch leer. Uptime Kuma erwartet sie, und die
    Beispiele der Bibliothek machen es genauso -- fehlende Felder haben dort schon zu
    Wartungen geführt, die angelegt waren und nie gegriffen haben.
    """
    dauer = int(daten.get("dauer_minuten") or 30)
    start = jetzt()
    ende = start + timedelta(minutes=dauer)
    fmt = "%Y-%m-%d %H:%M:%S"

    basis = {
        "active": True,
        "intervalDay": int(daten.get("intervall_tage") or 1),
        "dateRange": [start.strftime(fmt)],
        "weekdays": [],
        "daysOfMonth": [],
        "timezoneOption": daten.get("zeitzone") or ZEITZONE,
    }

    if strategie == "manual":
        # Kein Zeitplan. Wird über `active` an- und ausgeschaltet -- hier wird sie
        # stattdessen gelöscht, wenn die Arbeit vorbei ist.
        return MaintenanceStrategy.MANUAL, basis

    if strategie == "single":
        basis["dateRange"] = [start.strftime(fmt), ende.strftime(fmt)]
        return MaintenanceStrategy.SINGLE, basis

    if strategie == "recurring-interval":
        basis["timeRange"] = daten.get("zeitfenster") or [{"hours": 2, "minutes": 0}, {"hours": 3, "minutes": 0}]
        return MaintenanceStrategy.RECURRING_INTERVAL, basis

    if strategie == "recurring-weekday":
        basis["weekdays"] = daten.get("wochentage") or []
        basis["timeRange"] = daten.get("zeitfenster") or [{"hours": 2, "minutes": 0}, {"hours": 3, "minutes": 0}]
        return MaintenanceStrategy.RECURRING_WEEKDAY, basis

    if strategie == "recurring-day-of-month":
        basis["daysOfMonth"] = daten.get("monatstage") or []
        basis["timeRange"] = daten.get("zeitfenster") or [{"hours": 2, "minutes": 0}, {"hours": 3, "minutes": 0}]
        return MaintenanceStrategy.RECURRING_DAY_OF_MONTH, basis

    if strategie == "cron":
        basis["cron"] = daten.get("cron") or "0 2 * * *"
        basis["durationMinutes"] = dauer
        return MaintenanceStrategy.CRON, basis

    raise KumaFehler(400, f"Unbekannte Strategie {strategie!r}. Möglich: manual, single, "
                          "recurring-interval, recurring-weekday, recurring-day-of-month, cron")


def wartung_anlegen(schluessel, daten):
    titel = (daten.get("titel") or f"Wartung {schluessel}").strip()
    strategie = (daten.get("strategie") or "manual").strip()
    monitore = daten.get("monitore") or MONITOR_NAMEN
    seite = daten.get("statusseite") or STATUSSEITE
    beschreibung = (daten.get("beschreibung") or "").strip()

    def arbeit(api):
        # ERST die alte weg, dann die neue. Zweimal derselbe Schlüssel bedeutet Ersetzen,
        # nicht Verdoppeln -- sonst stehen nach fünf Updates fünf offene Wartungen für
        # dasselbe Gerät in der Liste.
        entfernt = []
        for w in unsere_wartungen(api).get(schluessel, []):
            try:
                api.delete_maintenance(w["id"])
                entfernt.append(w["id"])
            except Exception as e:
                log.warning("Alte Wartung %s ließ sich nicht löschen: %s", w.get("id"), e)

        strat, felder = felder_fuer(strategie, daten)
        try:
            antwort = api.add_maintenance(
                title=titel,
                description=beschreibung_mit_merker(beschreibung, schluessel),
                strategy=strat,
                **felder,
            )
        except UptimeKumaException as e:
            raise KumaFehler(502, f"Uptime Kuma hat das Anlegen abgelehnt: {e}")
        except Exception as e:
            raise KumaFehler(502, f"Anlegen fehlgeschlagen: {e}")

        wid = antwort.get("maintenanceID")
        if wid is None:
            raise KumaFehler(502, f"Uptime Kuma hat keine Wartungs-ID zurückgegeben: {antwort}")

        zuordnung = zuordnen(api, wid, monitore, seite)

        ablage = ablage_lesen()
        ablage[schluessel] = {
            "id": wid,
            "titel": titel,
            "strategie": strategie,
            "angelegt": jetzt().isoformat(timespec="seconds"),
        }
        ablage_schreiben(ablage)

        log.info("Wartung angelegt: %s (ID %s, Strategie %s), Monitore %s, Statusseite %s",
                 schluessel, wid, strategie,
                 ", ".join(zuordnung["monitore"]) or "keine", zuordnung["statusseite"] or "keine")
        if zuordnung["nicht_gefunden"]:
            log.warning("Diese Monitore gibt es in Uptime Kuma nicht: %s",
                        ", ".join(zuordnung["nicht_gefunden"]))
        return {
            "ok": True, "schluessel": schluessel, "id": wid, "titel": titel,
            "strategie": strategie, "ersetzt": entfernt, "zuordnung": zuordnung,
        }

    return mit_kuma(arbeit)


def wartung_beenden(schluessel):
    def arbeit(api):
        gefunden = unsere_wartungen(api).get(schluessel, [])
        geloescht = []
        for w in gefunden:
            try:
                api.delete_maintenance(w["id"])
                geloescht.append(w["id"])
            except Exception as e:
                raise KumaFehler(502, f"Wartung {w.get('id')} ließ sich nicht löschen: {e}")

        ablage = ablage_lesen()
        # Auch dann aus der Ablage nehmen, wenn in Uptime Kuma nichts zu löschen war --
        # sonst bleibt ein Eintrag stehen, der auf eine ID zeigt, die es nicht mehr gibt.
        ablage.pop(schluessel, None)
        ablage_schreiben(ablage)

        if not geloescht:
            log.info("Wartung beenden: für %s war keine offen.", schluessel)
        else:
            log.info("Wartung beendet: %s (IDs %s)", schluessel,
                     ", ".join(str(i) for i in geloescht))
        return {"ok": True, "schluessel": schluessel, "geloescht": geloescht}

    return mit_kuma(arbeit)


def aufraeumen():
    """Beim Start verwaiste Einträge beidseitig wegräumen.

    Zwei Richtungen, und beide sind nötig:

    1. Ablage zeigt auf eine Wartung, die es in Uptime Kuma nicht mehr gibt (jemand hat sie
       dort von Hand geloescht) -> Eintrag aus der Ablage nehmen.
    2. In Uptime Kuma steht eine Wartung mit unserem Merker, die in der Ablage fehlt -> in
       Uptime Kuma loeschen.

    Fall 2 ist der wichtigere: Er entsteht, wenn das Add-on oder der Host mitten in einer
    Wartung neu startet. Ohne dieses Aufräumen stünde sie für immer da, und die Statusseite
    wäre dauerhaft "in Wartung" -- schlimmer als ein kurzer Alarm, weil es niemandem auffällt.
    """
    def arbeit(api):
        ablage = ablage_lesen()
        vorhanden = unsere_wartungen(api)
        bericht = {"ablage_bereinigt": [], "kuma_geloescht": []}

        lebende_ids = {w["id"] for liste in vorhanden.values() for w in liste}
        for schluessel, eintrag in list(ablage.items()):
            if eintrag.get("id") not in lebende_ids:
                ablage.pop(schluessel, None)
                bericht["ablage_bereinigt"].append(schluessel)

        for schluessel, liste in vorhanden.items():
            if schluessel in ablage:
                continue
            for w in liste:
                try:
                    api.delete_maintenance(w["id"])
                    bericht["kuma_geloescht"].append({"schluessel": schluessel, "id": w["id"]})
                except Exception as e:
                    log.warning("Verwaiste Wartung %s nicht löschbar: %s", w.get("id"), e)

        ablage_schreiben(ablage)
        if bericht["ablage_bereinigt"] or bericht["kuma_geloescht"]:
            log.info("Aufgeräumt: %s", json.dumps(bericht, ensure_ascii=False))
        else:
            log.info("Aufräumen: nichts zu tun.")
        return bericht

    return mit_kuma(arbeit)


# --- HTTP-Schnittstelle -----------------------------------------------------------------------

def schluessel_pruefen():
    """Zugriffsschutz.

    Ohne gesetzten Schlüssel ist alles offen -- das ist bewusst erlaubt (Ersteinrichtung,
    abgeschottetes Netz), wird aber beim Start deutlich gemeldet. Wer ihn setzt, schickt ihn
    als Kopfzeile `X-Schlüssel` oder als `?schluessel=`.
    """
    if not ZUGRIFFSSCHLUESSEL:
        return None
    gegeben = request.headers.get("X-Schluessel") or request.args.get("schluessel") or ""
    if gegeben != ZUGRIFFSSCHLUESSEL:
        return jsonify({"ok": False, "fehler": "Zugriffsschlüssel fehlt oder ist falsch."}), 401
    return None


@app.errorhandler(KumaFehler)
def kuma_fehler(e):
    # Klartext und ein brauchbarer Code -- nicht stillschweigend nichts tun. Der Aufrufer
    # (das Wandpanel) schreibt diese Meldung in sein Protokoll, und dort muss sie erklären,
    # was zu tun ist.
    log.error("%s: %s", e.code, e.meldung)
    return jsonify({"ok": False, "fehler": e.meldung}), e.code


@app.get("/gesundheit")
def gesundheit():
    """Ohne Schlüssel erreichbar, damit Uptime Kuma dieses Add-on selbst überwachen kann.

    Gibt ausdrücklich keine Zugangsdaten und keine Kuma-Inhalte heraus -- nur, ob der Dienst
    läuft und ob er überhaupt konfiguriert ist.
    """
    bereit = bool(KUMA_BENUTZER and KUMA_PASSWORT) and not BIBLIOTHEK_FEHLER and not ABLAGE_FEHLER
    antwort = {
        # Wortwörtlich, weil die Überwachungsart "HTTP(s) - Keyword" Text sucht und nicht
        # Struktur -- dasselbe Schlüsselwort wie beim Wandpanel.
        "schluesselwort": "HAWALL-OK" if bereit else "HAWALL-FEHLER",
        "ok": bereit,
        "dienst": "wartungsmelder",
        "konfiguriert": bool(KUMA_BENUTZER and KUMA_PASSWORT),
        "bibliothek": BIBLIOTHEK_FEHLER or "geladen",
        "ablage": ABLAGE_FEHLER or "schreibbar",
        "geschuetzt": bool(ZUGRIFFSSCHLUESSEL),
        # Nur Anzahlen. Namen von Monitoren und Statusseiten sind Angaben über die
        # Überwachung, und diese Route ist ohne Schlüssel erreichbar.
        "monitore": len(MONITOR_NAMEN),
        "statusseite_gesetzt": bool(STATUSSEITE),
    }
    # Nur echte Fehler sind 503. Ein fehlender Zugriffsschlüssel ist keiner -- sonst meldet
    # die Überwachung ab dem ersten Tag "down" aus dem falschen Grund.
    return jsonify(antwort), (200 if bereit else 503)


@app.get("/selbsttest")
def selbsttest():
    """Prüft gegen die ECHTE Instanz, was funktioniert.

    Der Grund: uptime-kuma-api 1.2.1 verspricht offiziell nur Uptime Kuma 1.17-1.23.2, hier
    läuft 2.5.5. Am Quelltext von 2.5.5 geprüft heißen die Wartungs-Ereignisse unverändert,
    und add_maintenance gibt Felder durch -- es SOLLTE also gehen. "Sollte" ist aber keine
    Grundlage, und ein Selbsttest gegen die laufende Instanz ist der einzige Beweis.
    """
    fehler = schluessel_pruefen()
    if fehler:
        return fehler

    def arbeit(api):
        bericht = {"ok": True, "anmeldung": "gelungen"}
        try:
            info = api.info()
            bericht["kuma_version"] = info.get("version")
        except Exception as e:
            bericht["kuma_version"] = f"nicht lesbar ({e})"
        for name, ruf in [
            ("wartungen_lesen", lambda: len(api.get_maintenances())),
            ("monitore_lesen", lambda: len(api.get_monitors())),
            ("statusseiten_lesen", lambda: [s.get("title") for s in api.get_status_pages()]),
        ]:
            try:
                bericht[name] = ruf()
            except Exception as e:
                bericht[name] = f"FEHLER: {e}"
                bericht["ok"] = False

        # Der eigentliche Punkt dieses Selbsttests: Gibt es die eingetragenen Monitore und die
        # eingetragene Statusseite ÜBERHAUPT? Das ist der Fehler, der sonst unentdeckt bleibt,
        # weil alles gelingt -- die Wartung wird angelegt, sie steht in der Liste, und sie
        # unterdrückt keinen einzigen Alarm. Gemerkt hätte man es erst, wenn nachts beim
        # Update trotzdem ein Alarm kommt.
        try:
            vorhanden = {m.get("name") for m in api.get_monitors()}
            bericht["monitore_gefunden"] = [n for n in MONITOR_NAMEN if n in vorhanden]
            fehlend = [n for n in MONITOR_NAMEN if n not in vorhanden]
            if fehlend:
                bericht["monitore_NICHT_gefunden"] = fehlend
                bericht["ok"] = False
            if not MONITOR_NAMEN:
                bericht["monitore_gefunden"] = "KEINE eingetragen -- die Wartung würde keinen Alarm unterdrücken"
                bericht["ok"] = False
        except Exception as e:
            bericht["monitore_gefunden"] = f"FEHLER: {e}"
            bericht["ok"] = False

        if STATUSSEITE:
            try:
                seiten = api.get_status_pages()
                namen = {s.get("title") for s in seiten} | {s.get("slug") for s in seiten}
                if STATUSSEITE in namen:
                    bericht["statusseite_gefunden"] = STATUSSEITE
                else:
                    bericht["statusseite_NICHT_gefunden"] = (
                        f"{STATUSSEITE!r} -- vorhanden: " + ", ".join(sorted(str(n) for n in namen if n))
                    )
                    bericht["ok"] = False
            except Exception as e:
                bericht["statusseite_gefunden"] = f"FEHLER: {e}"
                bericht["ok"] = False
        else:
            bericht["statusseite_gefunden"] = "keine eingetragen -- die Wartung erscheint auf keiner Statusseite"

        return bericht

    return jsonify(mit_kuma(arbeit))


@app.get("/wartung")
def liste():
    fehler = schluessel_pruefen()
    if fehler:
        return fehler

    def arbeit(api):
        unsere = unsere_wartungen(api)
        return {
            "ok": True,
            "ablage": ablage_lesen(),
            "in_kuma": {s: [{"id": w["id"], "titel": w.get("title"), "aktiv": w.get("active")}
                            for w in liste_] for s, liste_ in unsere.items()},
        }

    return jsonify(mit_kuma(arbeit))


@app.post("/wartung/<schluessel>")
def anlegen(schluessel):
    fehler = schluessel_pruefen()
    if fehler:
        return fehler
    daten = request.get_json(silent=True) or {}
    with schloss:
        return jsonify(wartung_anlegen(schluessel, daten))


@app.delete("/wartung/<schluessel>")
def beenden(schluessel):
    fehler = schluessel_pruefen()
    if fehler:
        return fehler
    with schloss:
        return jsonify(wartung_beenden(schluessel))


@app.post("/aufraeumen")
def aufraeumen_route():
    fehler = schluessel_pruefen()
    if fehler:
        return fehler
    with schloss:
        return jsonify({"ok": True, "bericht": aufraeumen()})


def beim_start():
    """Aufräumen, aber der Dienst startet auch, wenn Uptime Kuma gerade nicht da ist.

    Nach einem Neustart des Hosts kommen Add-ons in beliebiger Reihenfolge hoch. Wer hier
    abbricht, weil Uptime Kuma noch nicht antwortet, hat ein Add-on, das nach jedem Stromausfall
    tot ist -- und das fällt niemandem auf, weil es ja nur im Fehlerfall gebraucht wird.
    """
    try:
        aufraeumen()
    except KumaFehler as e:
        log.warning("Aufräumen beim Start nicht möglich (%s). Der Dienst läuft trotzdem; "
                    "ueber POST /aufraeumen nachholbar.", e.meldung)
    except Exception as e:
        log.warning("Aufräumen beim Start fehlgeschlagen: %s", e)


if __name__ == "__main__":
    log.info("Wartungsmelder: Uptime Kuma %s, Monitore %s, Statusseite %s",
             KUMA_URL, ", ".join(MONITOR_NAMEN) or "(keine)", STATUSSEITE or "(keine)")
    if BIBLIOTHEK_FEHLER:
        log.error("uptime-kuma-api nicht geladen: %s", BIBLIOTHEK_FEHLER)
    threading.Thread(target=beim_start, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
