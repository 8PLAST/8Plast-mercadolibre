# Despliegue de 8PLAST en Railway

Esta versión conserva escritorio y portal local. Railway usa un único servicio con servidor HTTP, trabajador supervisado y volumen SQLite; no necesita Windows ni DPAPI. `python cloud_start.py` es el arranque común. Dockerfile prepara Linux; no ejecutar app.py (Tkinter) como servidor.

## Preparación en Railway

1. Conectar main de 8PLAST/8Plast-mercadolibre. Montar un volumen en `/data`. Si existen datos en otro volumen, respaldarlos antes de mover la ruta; no destruir el volumen anterior.
2. Cargar las variables de `.env.example`. Generar una clave Fernet para MELI_TOKEN_KEY; una contraseña fuerte PORTAL_PASSWORD (16+ caracteres) y una clave aleatoria PORTAL_SECRET_KEY (32+). No cambiar MELI_TOKEN_KEY en redeploys: cifra los tokens persistidos. APP_ENV=cloud, DATABASE_PATH y WEBHOOK_DATABASE_PATH=/data/8plast_stock.db. MELI_SYNC_ENABLED=0 durante toda la preparación. Los tres MELI_CLIENT_* / MELI_REDIRECT_URI corresponden a la aplicación real; la redirección debe ser exactamente `https://DOMINIO-REAL/oauth/callback` y estar registrada en Mercado Libre.
3. Start Command `python cloud_start.py`, healthcheck `/health`, una réplica, desactivar Serverless. Elegir política de reinicio Always si el plan lo permite. El supervisor reinicia web/worker caídos y termina el worker si no actualiza su estado durante 15 minutos. El trabajador permanece vivo en standby cuando no es propietario de la sincronización.
4. Generar dominio HTTPS. Ingresar con PORTAL_PASSWORD. El portal completo exige sesión; sólo healthcheck y webhook son públicos. No registrar un callback OAuth diferente del configurado. El webhook usa el dominio real + `/webhook/mercadolibre`.

## Traspaso sin dos responsables

No activar Railway ni autorizar/renovar allí mientras Windows sea responsable. MELI_SYNC_ENABLED=0 bloquea llamadas ML y modificaciones desde la web, aunque el backup importado tenga enabled=1. No existe coordinación mágica entre dos archivos SQLite en equipos distintos.

Primero desplegar y verificar HTTP, login, standby y persistencia. Cuando se confirme el traspaso: detener el worker Windows, crear un backup SQLite final consistente, y cargarlo en Gestión → Backup y migración inicial. No usar una copia antigua mientras el trabajador local haya seguido vendiendo. La importación sólo admite una base cloud sin productos ni movimientos; si ya se cargó una copia preliminar, no la sobrescribe: requiere conciliación aparte. No subir bases a GitHub.

La importación valida integridad, relaciones y tablas, crea backup de la base cloud y conserva las notificaciones recibidas allí. Productos, asociaciones, packs, movimientos, bandeja y checkpoints se copian con sus IDs. Las notificaciones de la PC no se transfieren: sus órdenes ya persistidas/checkpoints se conservan y la recuperación periódica cubre las fronteras.

Luego establecer MELI_SYNC_ENABLED=1, redeploy, e iniciar OAuth desde Gestión. Esto obtiene tokens propios para la instancia cloud y los guarda cifrados en `/data/meli_tokens.enc`. No exportar mercadolibre_secure.dat: sigue siendo sólo para el escritorio Windows. Activar la configuración de sincronización desde Gestión si no estaba activada en la base importada. No reiniciar el worker de Windows después del traspaso.

Verificar dos ciclos, una orden real, descuentos por pack, doble recuperación sin movimientos adicionales y un redeploy que conserve datos/tokens. Sólo entonces apagar la PC. Para gestión posterior desde esa PC, usar la URL Railway: la aplicación Tkinter sigue usando su base local y no debe mantenerse como segundo inventario editable.

## Operación

Las notificaciones se guardan antes del HTTP 200; el worker valida topic, ruta de orden y vendedor, obtiene la orden auténtica y la procesa mediante la bandeja. También consulta páginas por creación/actualización con checkpoints y superposición cada 300 segundos (configurable en Gestión). No modifica el stock publicado en ML. Cancelaciones permanecen para revisión y devoluciones confirmadas usan referencia única. Ventas reales pueden dejar saldo negativo; no se inventa producción.

El portal conserva las pantallas de análisis e inventario y agrega Gestión: productos, activación, mínimos/objetivos, producción en unidades o packs, movimientos, conciliación, asociaciones, exclusiones, devoluciones, presentaciones, importación XLSX/CSV, auditoría y descarga de backup. No elimina productos ni historial. Tiene sesión HTTPS, CSRF y límite de intentos de login. Los formularios de movimientos usan claves únicas contra reenvíos.

Respaldar el volumen y conservar MELI_TOKEN_KEY fuera del repositorio. Un 200 de /health verifica base accesible y heartbeat reciente, y publica standby/running/error; revisar además logs y checkpoints para certificar sincronización comercial exitosa. No interpretar standby como importación completa.

## Pruebas

`python -m unittest test_cloud_runtime test_ml_tokens test_stock test_readonly_portal -v`

Los tests cloud usan bases temporales y API simulada, nunca credenciales reales. Las pruebas históricas locales adicionales copian la base local y no se ejecutan sobre Railway. `tools/smoke_cloud.py` levanta servidor/worker en standby sobre una base temporal y comprueba reinicio de worker y persistencia. Los tests no certifican conectividad real de un deployment que aún no tenga variables/volumen.
