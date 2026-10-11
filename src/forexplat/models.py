"""Base común de los modelos de configuración de estrategias."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Rechaza claves desconocidas.

    Un error de tipeo en el YAML (por ejemplo `perido: 14`) debe fallar con un mensaje claro
    y no ignorarse en silencio dejando el valor por defecto.
    """

    model_config = ConfigDict(extra="forbid")
