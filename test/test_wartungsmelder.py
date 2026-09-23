#!/usr/bin/env python3
"""Tests für den Wartungsmelder -- ohne Uptime Kuma, ohne installierte Pakete.

Absichtlich kein pytest: Auf diesem Rechner wird nichts über den Paketmanager nachinstalliert.
Aufruf:

    python3 test/test_wartungsmelder.py

Unter test/attrappen/ liegen untergeschobene Fassungen von `flask` und `uptime_kuma_api`. Die
fälschen nur so viel nach, dass der Import gelingt. Die Attrappe von Kuma selbst
(`kuma_attrappe`) verhält sich bei `edit_maintenance` wie die echte Bibliothek 1.2.1:
Wartung lesen, übergebene Felder hineinmischen, zurückschreiben -- nachgelesen, nicht geraten.
"""

import os
import pathlib
import sys
import tempfile

HIER = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HIER / "attrappen"))
sys.path.insert(0, str(HIER.parent / "wartungsmelder"))

os.environ.update(
    KUMA_URL="http://kuma:3001", KUMA_BENUTZER="a", KUMA_PASSWORT="b",
    MONITOR_NAMEN="HA Wall Eurasburg", STATUSSEITE="eu", ZUGRIFFSSCHLUESSEL="",
)

import wartungsmelder as W                      # noqa: E402
from kuma_attrappe import Attrappe              # noqa: E402

GEZAEHLT = {"ok": 0}


def pruefe(bedingung, text):
    if not bedingung:
        print(f"  FEHLGESCHLAGEN: {text}")
        GEZAEHLT.setdefault("fehler", []).append(text)
    else:
        GEZAEHLT["ok"] += 1


def frisch():
    """Neue Attrappe, neue Ablage. Jeder Test faengt bei null an."""
    W.ABLAGE = str(pathlib.Path(tempfile.mkdtemp()) / "wartungen.json")
    kuma = Attrappe()
    W.mit_kuma = lambda arbeit: arbeit(kuma)
    return kuma


def anlegen(kuma, schluessel="wandpanel-vorort", dauer=10):
    return W.wartung_anlegen(schluessel, {
        "titel": "Hawall Eurasburg: Wartung",
        "beschreibung": "Das Gerät ist im Wartungsmodus. Es wird daran gearbeitet.",
        "strategie": "single", "dauer_minuten": dauer,
    })


# --- Die Beschreibung ------------------------------------------------------------------------

def test_kein_merker_in_der_beschreibung():
    """Der Merker stand auf der Statusseite -- Maschinenkram auf einer Seite für Menschen."""
    kuma = frisch()
    wid = anlegen(kuma)["id"]
    text = kuma.wartungen[wid]["description"]
    pruefe("Automatisch eingetragen" not in text, "Merker-Satz ist raus")
    pruefe("[wartungsmelder" not in text, "alte Klammer-Notation ist raus")
    pruefe(text.startswith("Das Gerät ist im Wartungsmodus"), "der Text kommt unverändert durch")


def test_alter_merker_wird_weiter_gelesen():
    """Wartungen aus 0.1.0 bis 0.1.3 tragen ihn. Ohne das wären sie nicht mehr zuzuordnen."""
    pruefe(W.merker_lesen("x\n\n[wartungsmelder:alt-schluessel]") == "alt-schluessel",
           "Klammer-Notation lesbar")
    pruefe(W.merker_lesen("x\n\nAutomatisch eingetragen und automatisch entfernt (satz-key).")
           == "satz-key", "Satz-Notation lesbar")
    pruefe(W.merker_lesen("nur Text") is None, "ohne Merker kommt None")
    pruefe(W.merker_lesen(None) is None, "und None wirft nicht")


# --- Beenden heisst Endzeit nachziehen -------------------------------------------------------

def test_beenden_zieht_die_endzeit_nach():
    """Der Kern: Die Wartung BLEIBT stehen, nur das Fenster wird verkürzt.

    Gelöscht wäre hinterher auch die Auskunft weg, dass überhaupt etwas war.
    """
    kuma = frisch()
    wid = anlegen(kuma)["id"]
    geplant = list(kuma.wartungen[wid]["dateRange"])

    W.wartung_beenden("wandpanel-vorort")

    pruefe(wid in kuma.wartungen, "die Wartung existiert noch")
    pruefe(kuma.geloescht == [], "es wurde nichts gelöscht")
    jetzt_bereich = kuma.wartungen[wid]["dateRange"]
    pruefe(jetzt_bereich[0] == geplant[0], "der Anfang bleibt unverändert")
    pruefe(jetzt_bereich[1] < geplant[1], "das Ende liegt früher als geplant")
    pruefe(W.ablage_lesen() == {}, "aus der Ablage ist sie raus")


def test_ende_ist_nie_vor_dem_anfang():
    """Bei einer verstellten Uhr könnte das Ende sonst VOR dem Anfang liegen -- Kuma lehnt ab.

    Und eine Wartung von null Sekunden sieht auf der Statusseite aus wie ein Fehler.
    """
    kuma = frisch()
    wid = anlegen(kuma)["id"]
    kuma.wartungen[wid]["dateRange"] = ["2099-01-01 12:00:00", "2099-01-01 12:30:00"]
    W.wartung_beenden("wandpanel-vorort")
    a, e = kuma.wartungen[wid]["dateRange"]
    pruefe(e > a, f"Ende ({e}) liegt nach dem Anfang ({a})")


def test_loeschen_nur_auf_ausdruecklichen_wunsch():
    kuma = frisch()
    wid = anlegen(kuma)["id"]
    W.wartung_beenden("wandpanel-vorort", loeschen=True)
    pruefe(wid not in kuma.wartungen, "mit loeschen=True ist sie weg")
    pruefe(kuma.geloescht == [wid], "und zwar über delete_maintenance")


def test_beenden_ohne_offene_wartung_wirft_nicht():
    kuma = frisch()
    r = W.wartung_beenden("gibt-es-nicht")
    pruefe(r["ok"] is True, "es ist kein Fehler, nichts zu beenden")
    pruefe(r["wartungen"] == [], "und es wurde nichts angefasst")


# --- Zuordnung und Ersetzen ------------------------------------------------------------------

def test_monitore_und_statusseite_werden_zugeordnet():
    """Ohne Zuordnung unterdrückt die Wartung keinen einzigen Alarm -- angelegt und wirkungslos."""
    kuma = frisch()
    wid = anlegen(kuma)["id"]
    arten = {art for art, _, _ in kuma.zuordnungen}
    pruefe(arten == {"monitore", "statusseite"}, f"beide Zuordnungen gemacht, nicht {arten}")
    for art, zu_id, ids in kuma.zuordnungen:
        pruefe(zu_id == wid, f"{art} an die richtige Wartung")
        pruefe(len(ids) == 1, f"{art}: genau ein Treffer")


def test_zweimal_derselbe_schluessel_ersetzt():
    """Sonst stehen nach fünf Updates fünf offene Wartungen für dasselbe Gerät."""
    kuma = frisch()
    erste = anlegen(kuma)["id"]
    zweite = anlegen(kuma)["id"]
    pruefe(erste != zweite, "die zweite ist eine neue Wartung")
    pruefe(erste not in kuma.wartungen, "die erste ist weg")
    pruefe(len(kuma.wartungen) == 1, f"es steht genau eine offen, nicht {len(kuma.wartungen)}")


def test_ablage_ist_die_zuordnung():
    """Seit der Merker weg ist, hängt das Wiederfinden an /data/wartungen.json."""
    kuma = frisch()
    wid = anlegen(kuma)["id"]
    pruefe(W.ablage_lesen()["wandpanel-vorort"]["id"] == wid, "die ID liegt in der Ablage")
    pruefe(list(W.unsere_wartungen(kuma)) == ["wandpanel-vorort"], "und wird darüber gefunden")


def test_aufraeumen_bereinigt_beide_richtungen():
    kuma = frisch()
    anlegen(kuma)
    # In Kuma von Hand gelöscht -> der Eintrag in der Ablage zeigt ins Leere.
    kuma.wartungen.clear()
    bericht = W.aufraeumen()
    pruefe(bericht["ablage_bereinigt"] == ["wandpanel-vorort"], "Ablage bereinigt")
    pruefe(W.ablage_lesen() == {}, "und danach leer")


# --- Strategien ------------------------------------------------------------------------------

def test_alle_strategien_bringen_ihre_felder_mit():
    daten = {"dauer_minuten": 45, "intervall_tage": 7, "wochentage": [1, 3],
             "monatstage": [1], "cron": "0 2 * * *"}
    erwartet = {
        "manual": [], "single": [], "recurring-interval": ["timeRange"],
        "recurring-weekday": ["timeRange"], "recurring-day-of-month": ["timeRange"],
        "cron": ["cron", "durationMinutes"],
    }
    for strategie, felder in erwartet.items():
        _, f = W.felder_fuer(strategie, daten)
        for feld in felder:
            pruefe(feld in f, f"{strategie}: {feld} ist dabei")
    _, single = W.felder_fuer("single", daten)
    pruefe(len(single["dateRange"]) == 2, "single hat Anfang UND Ende")
    _, manual = W.felder_fuer("manual", daten)
    pruefe(len(manual["dateRange"]) == 1, "manual hat nur den Anfang")


def test_unbekannte_strategie_sagt_klartext():
    try:
        W.felder_fuer("quatsch", {})
        pruefe(False, "eine unbekannte Strategie muss auffallen")
    except W.KumaFehler as e:
        pruefe(e.code == 400, "Code 400")
        pruefe("Unbekannte Strategie" in e.meldung, "und eine Meldung, die man lesen kann")


# --- Die Ablage selbst -----------------------------------------------------------------------

def test_kaputte_ablage_legt_den_dienst_nicht_lahm():
    frisch()
    pathlib.Path(W.ABLAGE).write_text("{kaputt", encoding="utf-8")
    pruefe(W.ablage_lesen() == {}, "eine kaputte Datei liest sich als leer")


def test_nicht_schreibbare_ablage_wird_gemeldet():
    """Sonst läuft alles weiter und die Zuordnung ist trotzdem verloren -- nur im Protokoll."""
    frisch()
    W.ABLAGE = "/gibt-es-nicht/tief/wartungen.json"
    pruefe(W.ablage_schreiben({"a": 1}) is False, "der Fehlschlag wird zurückgegeben")
    pruefe(W.ABLAGE_FEHLER is not None, "und steht in ABLAGE_FEHLER für /gesundheit")
    W.ABLAGE_FEHLER = None


# --- Schnittstelle ---------------------------------------------------------------------------

def test_routen_sind_da():
    soll = {
        ("GET", "/gesundheit"), ("GET", "/selbsttest"), ("GET", "/wartung"),
        ("POST", "/wartung/<schluessel>"), ("DELETE", "/wartung/<schluessel>"),
        ("POST", "/aufraeumen"),
    }
    ist = {(m, p) for m, p, _ in W.app.routen}
    pruefe(soll <= ist, f"fehlen: {soll - ist}")


if __name__ == "__main__":
    tests = [w for n, w in sorted(globals().items()) if n.startswith("test_") and callable(w)]
    for w in tests:
        print(f"• {w.__name__}")
        w()
    fehler = GEZAEHLT.get("fehler", [])
    print(f"\n{len(tests)} Tests, {GEZAEHLT['ok']} Zusicherungen erfüllt, {len(fehler)} fehlgeschlagen")
    sys.exit(1 if fehler else 0)
