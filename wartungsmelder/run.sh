#!/usr/bin/with-contenv bashio
# Die Optionen aus der Add-on-Oberfläche in Umgebungsvariablen übersetzen.
#
# Über bashio, nicht durch eigenes Lesen von /data/options.json: bashio kennt die Typen aus
# dem Schema (password, list) und liefert Listen als Zeilen statt als JSON-Bruchstück.

export KUMA_URL="$(bashio::config 'kuma_url')"
export KUMA_BENUTZER="$(bashio::config 'kuma_benutzer')"
export KUMA_PASSWORT="$(bashio::config 'kuma_passwort')"
export ZUGRIFFSSCHLUESSEL="$(bashio::config 'zugriffsschluessel' '')"
export STATUSSEITE="$(bashio::config 'statusseite' '')"
export ZEITZONE="$(bashio::config 'zeitzone' 'Europe/Berlin')"
export PROTOKOLLSTUFE="$(bashio::config 'protokollstufe' 'info')"
# Listen kommen zeilenweise; im Dienst wird an Zeilenumbrüchen getrennt.
export MONITOR_NAMEN="$(bashio::config 'monitor_namen' | tr '\n' '\036')"

if bashio::config.is_empty 'kuma_benutzer' || bashio::config.is_empty 'kuma_passwort'; then
  bashio::log.error "Benutzername oder Passwort für Uptime Kuma fehlen."
  bashio::log.error "Wartungen laufen über Socket.io und brauchen eine Anmeldung -- ein"
  bashio::log.error "API-Key genügt dafür NICHT, der gilt nur für /metrics."
  bashio::exit.nok
fi

if bashio::config.is_empty 'zugriffsschluessel'; then
  bashio::log.warning "Kein Zugriffsschlüssel gesetzt: Jeder im Netz kann hier Wartungen"
  bashio::log.warning "anlegen und löschen. Für den Betrieb bitte einen setzen."
fi

# localhost zeigt INNERHALB dieses Containers auf den Container selbst, nicht auf den Host.
# Ein Add-on, das "http://localhost:3001" befragt, fragt also sich selbst -- und bekommt
# "nicht erreichbar", obwohl Uptime Kuma laeuft. Das ist der erste Fehler, den man macht, und
# er sieht von aussen aus wie ein falsches Passwort.
case "${KUMA_URL}" in
  *localhost*|*127.0.0.1*)
    bashio::log.warning "kuma_url zeigt auf localhost. Innerhalb dieses Add-on-Containers ist"
    bashio::log.warning "das der Container SELBST, nicht der Host -- Uptime Kuma ist so nicht"
    bashio::log.warning "erreichbar. Bitte die IP des Home-Assistant-Hosts eintragen, z. B."
    bashio::log.warning "http://192.168.x.x:3001 (oder den Hostnamen des Kuma-Add-ons)."
    ;;
esac

bashio::log.info "Wartungsmelder startet, Uptime Kuma: ${KUMA_URL}"
exec python3 /app/wartungsmelder.py
