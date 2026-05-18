# Protección de datos

## Alcance

La aplicación analiza compras, facturas, ventas y stock de farmacia. No está diseñada para tratar datos de pacientes ni debe usarse para subir información clínica identificable.

## Datos tratados

Los datos previstos son:

- códigos nacionales;
- descripciones de artículos;
- unidades;
- importes brutos y netos;
- descuentos y cargos;
- proveedor o laboratorio;
- stock agregado;
- ventas agregadas por artículo.

## Datos no admitidos

No deben subirse:

- datos personales de pacientes;
- recetas identificables;
- documentos con DNI, teléfono, dirección o información clínica;
- facturas que incluyan datos personales no necesarios;
- capturas o PDFs con información sensible ajena al análisis económico.

## Procesamiento temporal

Los archivos se leen en memoria y los resultados temporales se almacenan en `st.session_state`. El botón "Borrar datos cargados" limpia la sesión y reinicia la aplicación.

La aplicación no debe guardar datos reales en disco, bases de datos, cachés persistentes ni archivos exportados salvo que se diseñe una funcionalidad específica y se revise legal y técnicamente.

## IA y datos agregados

Las recomendaciones asistidas por IA se generan a partir de datos agregados:

- totales de compra;
- descuentos medios;
- cargos agregados;
- resúmenes por bloque;
- especialidad cara agregada;
- contexto comercial no personal.

No se deben enviar documentos originales, tablas completas, nombres de archivo, datos personales, CIF ni datos de pacientes a modelos externos.

## Streamlit Cloud

En Streamlit Cloud los secretos deben configurarse en Secrets, no en el código. Para producción real se recomienda revisar:

- región y proveedor de hosting;
- acceso de usuarios;
- logs generados por la plataforma;
- política de retención;
- permisos del repositorio;
- contratos o acuerdos necesarios para tratamiento de datos.

## Recomendación para producción

Antes de usar datos reales de forma recurrente, desplegar en un entorno privado con control de acceso, gestión de secretos, cifrado, backups controlados y política explícita de no persistencia o retención mínima.
