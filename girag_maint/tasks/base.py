from ..core.context import TaskContext, TaskResult


class Task:
    """Clase base de toda tarea. Para añadir una nueva: heredar, implementar run()
    y registrarla en tasks/__init__.py."""
    id = ""
    title = ""
    description = ""
    icon = "?"            # letra mostrada en el cuadro naranja de la tarjeta
    button = "Ejecutar"
    needs_windows = True
    needs_admin = True

    def run(self, ctx: TaskContext) -> TaskResult:  # pragma: no cover
        raise NotImplementedError
