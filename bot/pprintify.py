from pprint import pp

def pprintify(obj) -> None:
    pp(dict(
        [attr, getattr(obj, attr)] for attr in dir(obj)
        if not attr.startswith('_') and not callable(getattr(obj, attr))
    ))