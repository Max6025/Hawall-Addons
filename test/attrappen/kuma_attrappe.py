"""Eine Attrappe von Uptime Kuma, die sich wie edit_maintenance in 1.2.1 verhaelt:
`get_maintenance` lesen, die uebergebenen Felder hineinmischen, zurueckschreiben."""


class Attrappe:
    def __init__(self):
        self.wartungen = {}
        self.naechste = 1
        self.monitore = [{'id': 1, 'name': 'HA Wall Eurasburg'}, {'id': 2, 'name': 'Anderes'}]
        self.seiten = [{'id': 9, 'title': 'Status Eurasburg', 'slug': 'eu'}]
        self.zuordnungen = []
        self.geloescht = []

    def login(self, *a):
        return {'token': 'x'}

    def disconnect(self):
        pass

    def get_maintenances(self):
        return [dict(w) for w in self.wartungen.values()]

    def get_maintenance(self, id_):
        return dict(self.wartungen[id_])

    def add_maintenance(self, **kw):
        wid = self.naechste
        self.naechste += 1
        self.wartungen[wid] = {'id': wid, **kw}
        return {'msg': 'Added.', 'maintenanceID': wid}

    def edit_maintenance(self, id_, **kw):
        w = self.wartungen[id_]
        w.update(kw)
        return {'msg': 'Saved.', 'maintenanceID': id_}

    def delete_maintenance(self, id_):
        self.geloescht.append(id_)
        self.wartungen.pop(id_, None)
        return {'msg': 'Deleted.'}

    def get_monitors(self):
        return [dict(m) for m in self.monitore]

    def get_status_pages(self):
        return [dict(s) for s in self.seiten]

    def add_monitor_maintenance(self, wid, ids):
        self.zuordnungen.append(('monitore', wid, ids))

    def add_status_page_maintenance(self, wid, ids):
        self.zuordnungen.append(('statusseite', wid, ids))

    def info(self):
        return {'version': '2.5.5'}
