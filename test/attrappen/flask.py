# Untergeschobenes Flask: nur so viel, dass der Import gelingt. Die Routen werden hier nicht
# aufgerufen -- getestet werden die reinen Funktionen darunter.
class _Req:
    headers = {}; args = {}
    def get_json(self, silent=False): return {}
request = _Req()
def jsonify(*a, **k): return a[0] if a else k
class Flask:
    def __init__(self, name): self.name = name; self.routen = []
    def _deko(self, methode, pfad):
        def d(f): self.routen.append((methode, pfad, f.__name__)); return f
        return d
    def get(self, p): return self._deko('GET', p)
    def post(self, p): return self._deko('POST', p)
    def delete(self, p): return self._deko('DELETE', p)
    def errorhandler(self, x):
        def d(f): return f
        return d
    def run(self, **k): pass
