# ADK importa este paquete y busca `root_agent`. Reexportarlo desde agent.py
# es la convencion (patron: agents_dir/{nombre}/agent.py con root_agent).
from . import agent  # noqa: F401
