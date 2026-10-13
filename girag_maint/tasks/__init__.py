from .bloatware import BloatwareTask
from .cleanup import CleanupTask
from .compact_os import CompactOsTask
from .dism_repair import DismRepairTask
from .glpi_sync import GlpiSyncTask
from .registry_repair import RegistryRepairTask
from .winget_upgrade import WingetUpgradeTask

# El orden define el orden de las tarjetas Y el orden de ejecución al usar
# "Ejecutar seleccionadas". La sincronización con GLPI va al final para reportar el estado final.
ALL_TASKS = [CleanupTask(), DismRepairTask(), BloatwareTask(), RegistryRepairTask(), CompactOsTask(),
             WingetUpgradeTask(), GlpiSyncTask()]
