# Base de conocimiento del agente

Lo que `buscar_documentacion` puede encontrar. Cada archivo de `html/` es un
documento del dataStore `orquestor-support-collection_documents`; el agente
responde solo con lo que esta aqui.

| Archivo | Que cubre |
| --- | --- |
| `html/sla_dxkare_v2.0.html` | SLA v2.0. De aqui lee el clasificador la matriz de severidad. No se genera: se versiona tal cual se subio. |
| `guia_asesorias.md` | Agendar, llamada o videollamada, entrar, reagendar, cancelar, sin horarios, menores |
| `guia_cuenta_y_acceso.md` | Registro, poliza o codigo corporativo, inicio de sesion, contraseña, cuenta bloqueada, instalar la app |
| `guia_receta_estudios_y_servicios.md` | Ver la receta, mandar estudios, especialidades, emergencias |
| `guia_membresia_y_familiares.md` | Invitar familiares, administrar invitados, membresias de pago |
| `PENDIENTES.md` | Lo que falta confirmar con el equipo. **No se sube al bucket.** |

Las guias salen de leer el codigo de `pwa-dxkre`: los textos entre comillas son
los de la app, literales, porque asi los busca el usuario y asi los reconoce en
pantalla. Si la PWA cambia un flujo, la guia queda desactualizada y el agente
da pasos que ya no existen: revisar la guia junto con el cambio.

Cada guia cierra con un anexo de sinonimos (como en el SLA). Es para la
busqueda: el usuario escribe "bajar la app" o "dar de alta a mis hijos", no
"instalar la PWA" ni "invitar a un familiar".

## Actualizar

Cambiar el `.md`, regenerar el HTML y subirlo. **No requiere deploy del
agente**: el engine lee el dataStore en cada busqueda.

```bash
.venv/bin/pip install -r requirements-dev.txt   # trae `markdown`
.venv/bin/python knowledge/build.py
```

Subir e importar (beta; para produccion, cambiar `architecture-beta` por
`architecture-production` en el bucket y en el proyecto):

```bash
gcloud storage cp knowledge/html/guia_*.html \
  gs://architecture-beta-orquestor-support-docs/ --content-type=text/html

curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "X-Goog-User-Project: architecture-beta" \
  -H "Content-Type: application/json" \
  "https://discoveryengine.googleapis.com/v1/projects/architecture-beta/locations/global/collections/default_collection/dataStores/orquestor-support-collection_documents/branches/default_branch/documents:import" \
  -d '{"gcsSource":{"inputUris":["gs://architecture-beta-orquestor-support-docs/guia_*.html"],"dataSchema":"content"},"reconciliationMode":"INCREMENTAL"}'
```

`INCREMENTAL` agrega o reemplaza las guias sin tocar el SLA. La operacion que
devuelve tarda un par de minutos; hasta que termina, la busqueda sigue viendo
la version anterior.

Para quitar una guia: borrarla del bucket y reimportar con
`"reconciliationMode":"FULL"` y `inputUris` `gs://<bucket>/*.html`, que deja en
el dataStore exactamente lo que hay en el bucket.
