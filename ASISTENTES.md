# Centro de asistentes · etapa 1

## Diagnóstico

El inventario activo fue migrado a Railway según README.md. La base local es histórica. Se revisaron el esquema SQLite, Flask, autenticación y CSRF del portal, importación y reprocesamiento de ventas, analítica y cálculo de rentabilidad. No se consultó la producción ni se usaron credenciales reales para comprobar acceso.

Ya existen asociaciones por publicación/variación con cantidad física, referencias únicas de movimientos, revisión de cancelaciones, checkpoints y análisis de ventas/producción. El cálculo de costos incorpora ambas caras y un espesor interno de 80 para rojas y amarillas. Esas funcionalidades se conservan. Faltaban preguntas, borradores, revisiones y registro de correcciones.

Lectura de la copia histórica: 90 productos, 185 asociaciones, 820 órdenes en bandeja y 552 movimientos. No existían tablas de preguntas. Estos conteos no representan el inventario actual en producción.

## Probar la primera versión

1. Abrir `iniciar_asistentes_preview.bat`.
2. Entrar en http://127.0.0.1:5081/asistentes desde esta PC.
3. Importar `assistant_import_example.json`: es un ejemplo ficticio, no datos de la cuenta.
4. Editar, guardar y aprobar. Volver a importar: la aprobación y el historial deben conservarse.

La vista previa usa `outputs/assistant_reviews_preview.db`, sin abrir el inventario. Está limitada a esta PC. La integración al portal autenticado está preparada en `/asistentes`, con diseño adaptable para celular, pero requiere un despliegue autorizado para acceder desde otros dispositivos. No se cambió infraestructura ni se desplegó.

## Lectura oficial de preguntas

La integración preparada usa GET `/users/me`, `/questions/search?seller_id=...&api_version=4` y `/items/MLA...`. Documentación: https://developers.mercadolibre.com.ar/preguntas-y-respuestas. Consulta manual, máximo 200 preguntas recientes ordenadas por fecha descendente, sin garantía de historial completo. En Railway reutiliza `MercadoLibreClient.access_token`, su bloqueo entre procesos y la renovación OAuth existente. Reintenta una sola vez ante HTTP 401. La renovación OAuth solo rota credenciales; no modifica recursos comerciales. No está verificada con la cuenta real.

La vista previa local no conecta a esa integración ni usa el archivo DPAPI histórico. Su botón de consulta está deshabilitado y muestra un enlace al portal activo. `/asistentes` debe estar desplegado allí para consultar datos reales. Una redirección al login no demuestra que esa ruta exista: la autenticación del portal intercepta también rutas desconocidas.

Las publicaciones se guardan como un subconjunto de atributos públicos. No se infiere cantidad, espesor o presentación desde títulos ni desde costos internos. Si hay variantes se requiere revisión humana. Importar archivos requiere que el operador verifique su origen y contenido; el formato usa `questions` y `publications` como en el ejemplo.

## Protecciones y límites

La herramienta solo acepta GET y una lista cerrada de rutas; rechaza cuerpos y redirecciones. No importa el procesador de órdenes ni tiene operaciones para enviar respuestas, modificar stock, precios o campañas. Las decisiones escriben exclusivamente en una base SQLite de revisión separada y se guardan en transacciones. No hay acción de ejecutar cambios externos después de aprobar. La gestión operativa existente del portal mantiene sus funciones y permisos; no es una herramienta de los asistentes.

Se conservan borrador original, edición, versión aprobada y transiciones con fecha. Las versiones impiden sobrescribir desde una página vieja. IDs repetidos no crean nuevas preguntas ni borran correcciones. Los ejemplos se muestran solo dentro de la misma publicación y se revisan manualmente; no constituyen aprendizaje automático ni reglas globales. Las respuestas publicadas recuperadas de Mercado Libre se muestran aparte.

Los borradores son determinísticos y conservadores. **Todavía no hay un modelo de IA conectado.** Peso, usos, resistencia, certificaciones, disponibilidad y entregas requieren información o revisión; no se prometen entregas. Los atributos importados pueden ser incorrectos: el responsable debe verificar antes de aprobar. La aprobación sigue siendo local y nunca se envía.

## Etapas pendientes

2. Conectar un proveedor de IA con salida estructurada y evidencia; incorporar información verificada por producto y ejemplos por publicación/presentación, sin herramientas de escritura externa.
3. Integrar rentabilidad y producción existentes a la bandeja; parámetros versionados, costos faltantes, markup, separación Full/propio y producción/material vendido. Auditar carritos, devoluciones, densidad, merma y el supuesto de bobina de 25 kg.
4. Catálogo, Ads, logística y reputación mediante fuentes oficiales autorizadas o archivos, con cobertura y limitaciones explícitas. Comparación de cargos volumétricos y modalidades según datos reales.
5. Coordinador con prioridades, evidencia, deduplicación y resumen diario. Estos módulos se identifican como pendientes en el panel, sin análisis ficticios.

Pruebas: `python -m unittest test_assistant_center test_marketplace_recovery test_marketplace_activation test_cloud_runtime`. Incluyen bloqueo de escritura externa antes de conectar, aislamiento de packs, preservación de correcciones, importación atómica, CSRF, escape de HTML y regresión de ventas/reprocesamiento.

Resultado de esta etapa: 33 pruebas aprobadas y simulación de activación aprobada. La comprobación de activación informó hash, stock y movimientos reales sin cambios y cero llamadas de red. No se verificó visualmente en un navegador ni se comprobó acceso real a Mercado Libre.

## Diagnóstico de carga de preguntas — corrección posterior

El iniciador ejecuta `assistant_preview.py`, que registraba el centro sin proveedor de tokens. La consulta fallaba antes de llamar a Mercado Libre. La base de revisiones local registraba únicamente el error genérico y `success=NULL`: el código anterior descartaba la causa, por lo que esos registros no permiten atribuir el fallo a un HTTP de la API.

Se agregaron errores clasificados y registros seguros: configuración local sin conexión, autorización ausente, configuración segura ilegible, integración en espera, token rechazado, permisos 403, recurso 404, límite 429, servicio remoto, conexión, timeout, formato inesperado y almacenamiento. Los registros solo contienen código, ruta sin parámetros y HTTP; nunca cuerpos de respuesta ni excepciones sin filtrar. El panel muestra último intento separado de última consulta exitosa, y conserva la fecha exitosa anterior ante fallos.

Las preguntas se conservan aunque una publicación responda 403 o 404: quedan sin atributos y con aviso para confirmar el producto. Una respuesta API sin `questions` no se interpreta como cero preguntas ni como éxito. El inventario no se consulta ni modifica desde la lectura de preguntas; las decisiones permanecen en `assistant_reviews.db`.

Verificación: 35 pruebas aprobadas de `test_assistant_center`, `test_ml_tokens` y `test_cloud_runtime`. Consulta pública a Railway: `/health` HTTP 200; `/asistentes` redirige al login. No hay CLI/connector Railway disponible, sesión autenticada de navegador ni acceso al volumen `/data/meli_tokens.enc` en este entorno. No se renovaron credenciales locales ni se consultó la cuenta real.

**Falta para verificar de extremo a extremo:** desplegar estos cambios en el servicio activo con autorización, entrar en una sesión del portal activo y ejecutar la consulta allí. No compartir secretos en el chat ni copiar tokens de Railway a Windows. La base de revisiones y los logs clasificados del servidor permitirán comprobar el resultado. La conexión real sigue sin verificarse.
