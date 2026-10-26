# Producción recomendada

Pestaña independiente `/produccion-recomendada`. Consulta el stock actual y las
recomendaciones aprobadas de ventas vinculadas de los últimos 90 días completos.
No modifica inventario, órdenes, historial, objetivos, precios ni publicaciones.

Cantidad orientativa en unidades = máximo de cero y el techo de:

    objetivo − (stock físico − reservas adicionales − demanda diaria × 5)
             − producción pendiente que llega hasta el día 5

La proyección admite saldo negativo para no perder la demanda faltante. El
objetivo ya incluye seguridad: no se agrega otra reserva al fabricar. La columna
de packs es una alternativa redondeada hacia arriba al pack de almacenamiento;
se muestra su equivalente real en unidades. Bolsas y rollos no se suman.

La producción recibida se carga mediante el formulario habitual de inventario.
Cada consulta lee de nuevo el stock: no hay caché ni un proceso que se deba
ejecutar manualmente. La página se actualiza cada minuto si está visible y no
hay filtros o simulaciones en edición. El botón Actualizar fuerza la consulta.
Cada plan lee productos, movimientos y ventas en una única transacción de lectura.

No existe registro de producción pendiente o reservas en el esquema actual.
La tabla muestra un escenario sobre stock físico y señala esa limitación.
Se puede simular un producto, indicando solo compromisos que no se hayan
descontado del stock e ingresos que no se hayan recibido. No se almacena ni
se crea una orden: la simulación vive en los parámetros de la vista y su CSV.
Si cambia el inventario se invalida toda simulación previa para no descontar
otra vez un ingreso ya reflejado en stock. El usuario debe volver a indicar
solo los pendientes realmente restantes. El CSV también aplica esta validación.

Los ingresos posteriores al día 5 se muestran como advertencia y no se descuentan
de la reposición actual. El riesgo se evalúa antes de cada ingreso conocido
hasta el día 5: una llegada tardía no oculta un agotamiento previo. Después del
día 5, los días de agotamiento son orientativos sin sumar ingresos posteriores.

Prioridades: CRÍTICO por agotamiento antes o al recibir; ALTA al llegar al punto
de reposición o a un día de él; NORMAL si corresponde fabricar; SIN NECESIDAD
si cubre el objetivo al recibir; REVISAR DATOS con stock negativo o sin demanda
verificada. La tabla se ordena por prioridad, agotamiento y demanda mensual.
Los saldos negativos exigen revisión, nunca se interpretan como stock disponible.

El promedio mensual es ventas verificadas de 90 días dividido por tres.
Disponibilidad histórica, reservas desconocidas y devoluciones sin conciliar
continúan visibles como advertencias del cálculo aprobado de la etapa 1.

Filtros por color, familia y prioridad; búsqueda por medida, nombre y SKU;
CSV con fecha, escenario, cantidades, pendientes y advertencias. El CSV conserva
los filtros y protege textos de catálogo que podrían interpretarse como fórmulas.
En móvil la tabla se presenta como fichas. No hay operaciones POST en esta pestaña.

Pruebas: cálculo de fabricación, prioridades, llegada tardía, no duplicación de
pendientes, reservas, redondeo de packs, filtros, CSV, rechazo de entradas inválidas,
recalculo tras ingreso de producción, invalidación de simulación y base sin cambios.
