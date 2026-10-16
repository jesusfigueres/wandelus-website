# Wandelus — web de demostración

Web de demo del negocio **Wandelus** (cadena ficticia de hoteles de playa de la Comunitat Valenciana, reserva directa) de MONBI. Estética de Claude Design (`static/*.html`, runtime
`support.js`); funcionalidad de MONBI (`app.py`, `static/monbi-site.js`).

- `app.py` — backend FastAPI: sirve la web, el catálogo (`data/catalog.json`, exportado de su Serpis con
  `engines/scripts/exporta_web_negocio.py` del repo `monbi`), el chat con el Salesman de MONBI, el alta, el foro
  y el tracking hacia su Serpis.
- Variables: `BUSINESS`, `SERPIS_URL`, `SERPIS_INGEST_TOKEN`, `ENGINES_URL`, `ENGINES_TOKEN`, `GTM_ID` (opcional).
- Despliegue: servicio `wandelus-website` de Railway; se ve en `demos.monbi.ai/websites/wandelus/`.

Catálogo y precios ficticios (mundo sintético de la demo).
