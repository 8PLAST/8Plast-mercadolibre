# Ventas Full: excepción prospectiva al descuento físico

Un único inventario, sin depósitos, pestañas ni migraciones de tablas nuevas.
Las estadísticas, stock objetivo y producción recomendada siguen incluyendo
las ventas Full vinculadas: sus algoritmos no se modifican.

La primera ejecución del trabajador actualizado crea en el volumen persistente
`full_stock_policy.json` con una fecha UTC de vigencia. No se incluye en Git ni
Docker y no se reemplaza en reinicios. El heartbeat y `/health` permiten verificar
`full_stock_effective_from`. No se cambia la configuración de sincronización.

Para órdenes pagadas/confirmadas creadas a partir de esa fecha se consulta el
envío real mediante GET `/shipments/{shipping.id}`, usando `x-format-new: true`.
Si la orden no tiene el ID, se resuelve mediante GET `/orders/{id}/shipments`
con `X-New-Domain: true`, seleccionando exclusivamente `type=forward`.

Se valida identidad del envío y consistencia de la logística. `fulfillment`
retorna `full_skipped` sin ejecutar movimientos ni cambiar stock. La bandeja
usa `FULL_NO_STOCK` para indicar resolución sin salida física. Flex
(`self_service`) y las otras logísticas reconocidas de despacho propio
conservan los factores, asociaciones, exclusiones y claves contra duplicados.

Faltantes, tipos desconocidos, respuestas contradictorias o errores de la API
dejan el descuento sin ejecutar y la orden en ERROR para el reintento existente.
No se interpreta falta de shipment_id como despacho propio. Estos casos se
ven en la gestión ya existente; no se agregan pantallas.

La misma validación se aplica desde la recuperación periódica y la cola de
notificaciones. Una llamada directa a la base sin resolver logística no puede
descontar una orden nueva sujeta a la regla. Una cancelación mantiene su
tratamiento previo, que no repone stock automáticamente.

La vigencia se compara con la creación de la orden, no con su llegada por
webhook, su última actualización ni un reinicio. Antes de la vigencia se conserva
la lógica e idempotencia anteriores. No se recorre el historial para reclasificar,
no se revierten ventas ni se crean compensaciones. La auditoría histórica se
dejó detenida por indicación del usuario: solo rige hacia adelante.

Pruebas: Full sin movimiento y contada en demanda; Flex/resto con factor y
sin duplicados; límite inclusivo de fecha; persistencia de vigencia; recuperación
periódica; reintento tras API caída; encabezados GET; formato nuevo; selección
forward frente a devoluciones; error por identidad/logística contradictoria;
movimientos anteriores idénticos antes y después. Todas usan bases temporales
y respuestas API simuladas. No se crean ventas reales para probar.
