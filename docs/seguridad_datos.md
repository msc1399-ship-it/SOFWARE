# Seguridad de datos

## Principios

La aplicación está diseñada para procesar documentación de farmacia de forma temporal durante la sesión de Streamlit. Los archivos subidos se leen en memoria con pandas y no se escriben en disco desde la aplicación.

## No persistencia

- No se guardan albaranes, facturas, ventas ni stock en el repositorio.
- No se generan copias internas, backups, bases SQLite, pickle, CSV o Excel con datos reales.
- No se usa `st.cache_data` ni `st.cache_resource` para datos subidos por el usuario.
- Los resultados intermedios viven solo en `st.session_state` y se eliminan con el botón "Borrar datos cargados".

## Control de acceso

- La app exige contraseña general antes de cargar datos.
- La contraseña debe configurarse en Streamlit Secrets con `APP_PASSWORD`.
- El modo auditor usa una clave separada en `DEBUG_PASSWORD`.
- No deben incluirse claves reales en el código ni en el repositorio.

## Interfaz y privacidad

En modo producción se deben mostrar solo KPIs, resúmenes, conciliaciones y resultados agregados. Las tablas completas o información técnica quedan reservadas para `MODO_DEBUG`.

No deben mostrarse nombres reales de archivos, trazas técnicas, sets internos de conciliación o tablas completas salvo en modo auditor y cuando sea necesario para revisión.

## Validación de archivos

Los datos de farmacia deben subirse en formato `.xlsx`. La app valida extensión y tamaño máximo antes de procesar los archivos. Si un archivo no cumple la validación, se descarta sin romper el resto de la sesión.

## IA

El módulo de recomendaciones asistidas por IA trabaja con información agregada y anonimizada. No debe enviar documentos completos, facturas, albaranes, nombres de archivo, CIF, datos personales ni datos de pacientes.

La primera versión usa reglas locales por privacidad. La integración con API externa queda preparada, pero debe revisarse antes de activar envío de datos fuera del entorno.

## Hosting

Streamlit Cloud es adecuado para pruebas controladas, pero para uso real con datos de farmacia se recomienda un hosting con:

- control de acceso corporativo;
- aislamiento por cliente;
- secretos gestionados;
- cifrado en tránsito;
- políticas de retención;
- monitorización de accesos;
- acuerdos de tratamiento de datos si aplica.
