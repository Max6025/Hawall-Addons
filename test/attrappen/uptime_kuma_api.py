# Untergeschobene Bibliothek: nur so viel, dass der Import gelingt und die reinen Funktionen
# pruefbar sind. Sie faelscht NICHTS nach -- was Kuma beruehrt, wird hier nicht getestet.
class MaintenanceStrategy:
    MANUAL = 'manual'; SINGLE = 'single'
    RECURRING_INTERVAL = 'recurring-interval'
    RECURRING_WEEKDAY = 'recurring-weekday'
    RECURRING_DAY_OF_MONTH = 'recurring-day-of-month'
    CRON = 'cron'
class UptimeKumaException(Exception): pass
class UptimeKumaApi:
    def __init__(self, *a, **k): raise RuntimeError('im Test nicht verbunden')
