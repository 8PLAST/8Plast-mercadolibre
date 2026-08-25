# 8Plast Stock

Aplicación local de gestión de stock para bolsas cortadas y rollos bolsa tubo.

## Cómo abrirla

1. Hacé doble clic en `iniciar_8plast.bat`.
2. La información se guarda automáticamente en `8plast_stock.db`, dentro de esta misma carpeta.

En esta computadora la aplicación puede usar el entorno ya incluido con Codex. Si el iniciador indica que no encontró Python, instalá **Python 3** desde <https://www.python.org/downloads/> marcando **Add Python to PATH**, y volvé a abrirlo.

No necesita internet, servidor, usuario ni contraseña. No cierres la ventana negra mientras uses la aplicación.

## Primeros pasos

1. Entrá en **Bolsas** o **Rollos** y pulsá **Nuevo producto**.
2. Indicá el stock inicial, o dejalo en cero y cargalo luego con **Cargar producción**.
3. Para una bolsa podés cargar producción en bolsas individuales o en packs físicos. El stock real siempre queda expresado en bolsas.
4. Para ventas y otras salidas, usá **Registrar movimiento**. El sistema impide que el stock quede negativo.
5. Consultá todos los cambios en **Movimientos**.

## Criterio de stock

- Una bolsa tiene un único stock maestro en bolsas individuales.
- Cada bolsa tiene un **Pack de almacenamiento** configurable. El valor inicial es 50, pero puede editarse a 25 o cualquier entero positivo. Nunca se cambia automáticamente por los micrones.
- **Packs físicos** es `stock maestro ÷ pack de almacenamiento`, sin decimales.
- Las columnas comerciales x50, x100, x200, x300 y x500 son cálculos separados (`stock maestro ÷ cantidad`). No son depósitos ni packs físicos.
- Una venta x200 debe registrarse como una salida de 200 bolsas.
- Un rollo tiene su stock maestro en cantidad de rollos.
- Las presentaciones adicionales se agregan desde **Configuración** y aparecen al volver a abrir la aplicación.

## Stock mínimo y objetivo

- **Stock real** es la cantidad física que existe en 8Plast.
- **Stock mínimo** es el punto en el que conviene comenzar a producir.
- **Stock objetivo** es la cantidad física a la que se quiere llegar con la reposición.
- Estados: **OK** por encima del mínimo, **PRODUCIR** cuando queda stock pero está en el mínimo o por debajo, y **SIN STOCK** cuando llega a cero.
- El panel muestra cuánto falta para alcanzar el objetivo. Para bolsas también muestra cuántos packs físicos se necesitan, redondeando hacia arriba cuando corresponda.
- La carga de producción permite elegir bolsas individuales o packs físicos. Por ejemplo, 10 packs de un producto configurado x25 agregan 250 bolsas en un único movimiento.

## Seguridad y copias

- Cada modificación de stock crea un movimiento histórico dentro de la misma operación de base de datos.
- La base rechaza stock negativo y bloquea cambios de stock que no tengan movimiento.
- Los productos se desactivan; sus movimientos no se borran.
- Usá **Configuración → Crear copia ahora** periódicamente y guardá el archivo en otra ubicación.

## Preparación para MercadoLibre

El stock publicado y el stock físico son conceptos independientes: una publicación puede mostrar una cantidad comercial alta sin cambiar el stock real de 8Plast.

La base ya incluye:

- asociaciones de varias publicaciones con un mismo producto físico;
- ID de publicación y de variación;
- cantidad física consumida y tipo de presentación;
- cantidad publicada conocida y fecha de última sincronización;
- modo `SOLO_DESCONTAR_VENTAS` (predeterminado) o `SINCRONIZAR_STOCK`;
- referencia externa única para impedir que una orden o venta se descuente dos veces.

En el modo predeterminado, una venta descontará el stock físico y generará un único movimiento, pero no reemplazará la cantidad publicada en MercadoLibre. La pantalla **MercadoLibre** permite crear varias asociaciones para un mismo producto, autorizar la cuenta y leer las 50 órdenes más recientes. Las órdenes repetidas y cancelaciones repetidas no duplican movimientos. Esta versión no contiene ninguna llamada para modificar publicaciones.

## Cómo conectar MercadoLibre

La preparación técnica está terminada, pero para conectarla necesitás credenciales reales propias:

1. Ingresá al portal [MercadoLibre Developers](https://developers.mercadolibre.com.ar/) con la cuenta administradora de 8Plast y creá una aplicación.
2. Habilitá **Acceso offline**. MercadoLibre recomienda OAuth con Authorization Code; si el portal ofrece PKCE, dejalo desactivado para esta primera conexión local.
3. MercadoLibre exige actualmente una **Redirect URI HTTPS, estática y exactamente coincidente**. Necesitás indicar una página HTTPS bajo tu control que pueda recibir la redirección y mostrar/conservar los parámetros `code` y `state`. No uses una dirección inventada. Para notificaciones automáticas también hará falta más adelante una URL HTTPS pública.
4. Copiá el **App ID/Client ID**, el **Client Secret** y la **Redirect URI** exacta.
5. En 8Plast Stock abrí **MercadoLibre → Configurar conexión**, ingresá esos tres datos y pulsá **Guardar credenciales**. El secreto y los tokens quedan cifrados con la cuenta actual de Windows y no están en el código fuente.
6. Pulsá **Abrir autorización**, iniciá sesión con la cuenta administradora y aceptá el permiso.
7. Al llegar a tu Redirect URI, copiá la URL completa del navegador y pegala en el segundo campo de la aplicación. Pulsá **Completar conexión**.
8. Creá las asociaciones de publicaciones antes de leer ventas. En **Cantidad física** indicá bolsas o rollos consumidos por cada unidad comprada.
9. Pulsá **Leer ventas ahora**. La primera lectura revisa hasta 50 órdenes recientes; verificá el resultado y el historial.

MercadoLibre documenta que el código se intercambia en `/oauth/token`, que los tokens deben enviarse por el encabezado de autorización y que el refresh token se reemplaza al renovarlo. La aplicación implementa ese flujo y guarda siempre el último token. Consultá la [autenticación oficial](https://developers.mercadolibre.com.ar/es_ar/administra-areas-de-cobertura/autenticacion-y-autorizacion), la [gestión oficial de órdenes](https://developers.mercadolibre.com.ar/es_ar/gestiona-ventas/gestiona-ventas) y la [guía oficial de notificaciones](https://developers.mercadolibre.com.ar/es_ar/atributos-y-variaciones/productos-recibe-notificaciones).

### Cancelaciones, devoluciones y notificaciones

- Si una orden previamente descontada aparece luego con estado `cancelled`, la lectura reintegra exactamente el movimiento original una sola vez.
- La base y la lógica de movimientos ya soportan devoluciones únicas por referencia externa.
- Para detectar devoluciones parciales automáticamente y recibir ventas en tiempo real se deberá configurar un webhook público HTTPS con el tópico `orders_v2` y, según el caso, recursos de devoluciones. Una aplicación que corre solamente dentro de esta PC no puede recibir notificaciones públicas por sí sola. Hasta contar con esa URL, usá **Leer ventas ahora**.
- No actives una asociación hasta confirmar producto, variación y cantidad consumida. Si una venta supera el stock físico disponible, se rechaza sin dejar el stock negativo y debe resolverse manualmente.

## Actualizaciones y conservación de datos

Al abrir una versión nueva, la estructura de la base se actualiza sin borrar productos, stock ni movimientos. Antes de esta integración se creó automáticamente una copia `8plast_stock_pre_ml_FECHA_HORA.db` en esta carpeta.

## Webhook MercadoLibre

El webhook es un pequeño servidor separado de la aplicación de stock. Recibe las notificaciones de MercadoLibre en `/webhook/mercadolibre`, las guarda y responde inmediatamente. **Todavía no consulta órdenes ni modifica stock.**

Guarda fecha de recepción, tópico, recurso, usuario, aplicación, número de intento, fechas informadas por MercadoLibre y el JSON completo. Cada evento lógico queda una sola vez en `mercadolibre_webhook_events`; cada reintento queda auditado en `mercadolibre_webhook_deliveries`. Los eventos incompletos también se conservan, marcados para revisión.

### Probarlo localmente

1. Hacé doble clic en `iniciar_webhook.bat` y dejá abierta esa ventana.
2. Abrí <http://localhost:8000/health>. Debe mostrar `{"status":"ok"}`.
3. Para enviar una notificación de prueba desde PowerShell, abrí otra ventana en esta carpeta y ejecutá:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8000/webhook/mercadolibre -ContentType application/json -Body '{"topic":"orders_v2","resource":"/orders/123456","user_id":123,"application_id":456,"attempts":1,"sent":"2026-08-25T20:00:00Z","received":"2026-08-25T20:00:01Z"}'
```

Si el iniciador indica que falta Flask, ejecutá una sola vez `python -m pip install -r requirements.txt`. Localmente los eventos se agregan a la base actual `8plast_stock.db`, sin tocar productos, movimientos ni stock.

### Variables de entorno

El archivo `.env.example` enumera las variables admitidas, pero la aplicación no carga automáticamente archivos `.env` y no se creó ninguno con secretos. `.env`, bases y copias están excluidos de Git.

- `WEBHOOK_DATABASE_PATH`: ubicación de SQLite. Si se omite localmente, usa la base principal.
- `MELI_CLIENT_ID`, `MELI_CLIENT_SECRET` y `MELI_REDIRECT_URI`: reservadas para la siguiente etapa. **No son necesarias para recibir notificaciones y no deben configurarse todavía.**

### Subir el proyecto a GitHub

1. Creá un repositorio vacío y privado en GitHub. No agregues README ni otros archivos desde la web.
2. Abrí una terminal en esta carpeta.
3. Ejecutá, reemplazando la dirección por la de tu repositorio:

```text
git init
git add .
git commit -m "Agregar webhook de MercadoLibre"
git branch -M main
git remote add origin https://github.com/TU-USUARIO/8plast-stock.git
git push -u origin main
```

Antes de confirmar, `git status` no debe mostrar `.env`, `8plast_stock.db`, archivos de backup ni `mercadolibre_secure.dat`.

### Desplegarlo en Render

El archivo `render.yaml` deja preparado un Blueprint con Flask, Gunicorn, `/health` y un disco persistente de 1 GB:

1. Creá una cuenta en [Render](https://render.com/) y conectá GitHub.
2. Elegí **New → Blueprint**.
3. Seleccioná el repositorio y la rama `main`.
4. Render detectará `render.yaml`. Revisá el servicio `8plast-mercadolibre-webhook` y confirmá el despliegue.
5. No hace falta ingresar Client ID, Client Secret ni Access Token en esta etapa.
6. Esperá que `/health` figure saludable.

Render usa normalmente un sistema de archivos temporal. Por eso este Blueprint solicita el plan `starter` y un disco persistente: sin ese disco, los eventos SQLite podrían desaparecer al reiniciar o desplegar. El servicio de Render guarda una base propia en la nube; no contiene ni sincroniza automáticamente el catálogo o stock de la PC.

La configuración sigue las guías oficiales para [desplegar Flask](https://render.com/docs/deploy-flask), [servicios web y discos](https://render.com/docs/web-services) y [Blueprints](https://render.com/docs/infrastructure-as-code).

### URL para MercadoLibre Developers

Cuando Render muestre el dominio final, copiá exactamente:

```text
https://NOMBRE-REAL-DEL-SERVICIO.onrender.com/webhook/mercadolibre
```

Pegalo en **Notificaciones callbacks URL** y habilitá el tópico recomendado `orders_v2`. No uses literalmente `NOMBRE-REAL-DEL-SERVICIO`: reemplazalo por el subdominio asignado por Render. MercadoLibre documenta que las notificaciones incluyen `resource`, `user_id`, `topic`, `application_id`, `attempts`, `sent` y `received`, y luego permiten consultar el recurso real con autenticación. Consultá la [documentación oficial de notificaciones](https://developers.mercadolibre.com.ar/es_ar/atributos-y-variaciones/productos-recibe-notificaciones).
