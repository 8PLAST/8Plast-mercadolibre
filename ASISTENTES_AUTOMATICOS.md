# Preguntas y avisos privados de 8Plast

El arranque existente de Railway supervisa un proceso adicional `assistant_worker.py`.
No necesita una computadora local y es independiente del ajuste de ventas de 300 s.
Comparte únicamente el mecanismo de autenticación/renovación OAuth existente.
El rol `MELI_SYNC_ENABLED=1` sigue identificando al propietario autorizado de la integración.
No se modifica la sincronización de ventas ni se abre el inventario desde el monitor.

## Configuración segura

En el servicio existente del portal de Railway, agregar en **Variables**:

- `TELEGRAM_BOT_TOKEN`: token del bot privado dedicado entregado por @BotFather.
- `OPENAI_API_KEY`: clave de proyecto OpenAI API con saldo disponible. No es la suscripción ChatGPT.
- Opcional `ASSISTANT_AI_MODEL` (predeterminado `gpt-4.1-mini`).
- Comprobar `MELI_REDIRECT_URI`: URL HTTPS completa terminada en `/oauth/callback`,
  exactamente igual a la registrada en DevCenter. Una URI sin esquema se rechaza antes de iniciar OAuth.

No guardar esos secretos en repositorio, archivos de preguntas, chats, URLs o logs.
No hace falta cargar un `chat_id` manual. En `/asistentes`, Conectar mi celular genera
un enlace de un solo uso, válido 15 minutos. El dueño abre ese enlace en Telegram y
pulsa Iniciar. Solo un chat privado que presente ese desafío puede vincularse;
un extraño o grupo no puede convertirse en destinatario por hablarle al bot.
Un bot existente con webhook/otro consumidor puede responder 409: usar un bot dedicado.

## Funcionamiento

Activar detección desde el panel autenticado. Primera consulta exitosa: carga histórica
silenciosa y fija el inicio. Solo preguntas UNANSWERED con fecha posterior verificable
generan avisos. Consultas al recurso oficial `/my/received_questions/search`, cada 60 s
después de terminar el ciclo. Latencia adicional depende de las APIs y de la cola.
Se recuperan hasta 200 preguntas recientes; si esa ventana no cubre el intervalo
se muestra aviso de cobertura incompleta, sin prometer detección completa.

Los atributos públicos se consultan por publicación y se almacenan en la base
de revisión con caché de 15 minutos. Si Mercado Libre impide leer una publicación,
la pregunta sigue visible, pero su borrador exige confirmar los datos del producto.
La IA clasifica intención y selecciona atributos: el programa arma la respuesta
con valores explícitos verificados. No se inventan especificaciones desde títulos,
ni se prometen stock, precio, entrega, certificaciones o usos. Sin clave o ante fallo
de IA, queda borrador conservador con el estado real de generación en el panel.
Se generan borradores para preguntas nuevas en la cola y la pregunta real elegida
para una prueba; no se consume IA sobre todo el historial automáticamente.

## Persistencia y duplicados

Todo el estado vive en `/data/assistant_reviews.db`, separado de inventario.
Aviso único por ID de pregunta; no vuelve a generarse al reiniciar o consultar otra vez.
Antes de enviar Telegram se registra CLAIMED en una transacción. Si se pierde la
confirmación o se interrumpe el proceso, se marca UNKNOWN: no se reintenta ciegamente.
Esto evita duplicados, pero puede exigir revisar manualmente un aviso cuya entrega
es incierta. Solo un 429 explícito reencola luego del tiempo indicado.
SENT significa aceptado por Telegram, no leído ni recibido en el dispositivo.
Errores solo almacenan códigos seguros; nunca cuerpos de error, tokens o URLs del bot.
Las ediciones y decisiones humanas se preservan, incluso si se editan durante la generación.

## Verificación real requerida

1. Repetir consulta real autenticada y distinguir fallo de una consulta exitosa vacía.
2. Activar monitor y comprobar avance de la última consulta automática en el panel.
3. Vincular Telegram, enviar PRUEBA, comprobar en el celular y confirmar recepción.
4. La PRUEBA incluye una pregunta real cargada si existe; de lo contrario declara
   que detección y borrador aún no fueron verificados.
5. Una nueva pregunta real posterior al inicio debe producir un aviso único con
   pregunta, publicación, borrador y enlace autenticado. No crear una pregunta falsa
   a un cliente ni enviar una respuesta para simular esta prueba.

Los otros asistentes están pausados. Aprobar solo registra una decisión local;
no existe una herramienta de envío de respuestas ni de cambios comerciales ML.
