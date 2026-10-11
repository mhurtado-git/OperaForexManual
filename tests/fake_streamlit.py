"""Streamlit falso: permite ejecutar app.py sin tener Streamlit instalado y comprobar qué dibuja."""

import sys
import types


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        return lambda *a, **k: _Ctx()


class FakeStreamlit(types.ModuleType):
    def __init__(self, clicked=(), values=None, uploaded=None):
        super().__init__("streamlit")
        self.clicked = set(clicked)
        self.values = values or {}      # key -> valor devuelto por un widget
        self.uploaded = uploaded
        self.session_state = {}
        self.calls = []                  # (nombre, texto o datos principales)

    def _log(self, name, *args):
        self.calls.append((name, args))

    def texts(self, name):
        return [str(args[0]) for call, args in self.calls if call == name and args]

    # --- widgets que devuelven valores ---
    def button(self, label, key=None, **kwargs):
        self._log("button", label)
        return key in self.clicked

    def selectbox(self, label, options, index=0, key=None, **kwargs):
        return self.values.get(key, options[index])

    def multiselect(self, label, options, default=None, key=None, **kwargs):
        return self.values.get(key, list(default or []))

    def radio(self, label, options, index=0, key=None, **kwargs):
        return self.values.get(key, options[index])

    def number_input(self, label, value=0, key=None, **kwargs):
        return self.values.get(key, value)

    def slider(self, label, min_value, max_value, value, key=None, **kwargs):
        return self.values.get(key, value)

    def checkbox(self, label, value=False, key=None, **kwargs):
        return self.values.get(key, value)

    def file_uploader(self, label, key=None, **kwargs):
        return self.uploaded

    def tabs(self, labels):
        return [_Ctx() for _ in labels]

    def columns(self, spec):
        return [_Ctx() for _ in range(spec if isinstance(spec, int) else len(spec))]

    def spinner(self, text=""):
        return _Ctx()

    # --- salidas que interesa registrar ---
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def method(*args, **kwargs):
            self._log(name, *args)
            return _Ctx()

        return method


def _forget_app():
    """Olvida app.py para que se vuelva a importar con el Streamlit falso de cada prueba."""
    for module in [m for m in sys.modules if m.startswith("forexplat.ui.app")]:
        del sys.modules[module]
    package = sys.modules.get("forexplat.ui")
    if package is not None and hasattr(package, "app"):
        delattr(package, "app")


def install(**kwargs):
    fake = FakeStreamlit(**kwargs)
    sys.modules["streamlit"] = fake
    _forget_app()
    return fake


def uninstall():
    sys.modules.pop("streamlit", None)
    _forget_app()
