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

bashio::log.info "Wartungsmelder startet, Uptime Kuma: ${KUMA_URL}"
exec python3 /app/wartungsmelder.py
